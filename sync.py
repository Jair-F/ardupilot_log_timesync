#!/bin/python3
import argparse
import sys
import time
from multiprocessing import Pool

import argcomplete
import numpy
import numpy.typing as npt
from astropy.time import Time, TimeDelta
from colorama import Fore, Style, init
from mcap.reader import make_reader
from mcap.writer import CompressionType, Writer
from pymavlink import mavutil
from scipy.interpolate import interp1d


class MissingDataError(Exception):
    pass


def gps_time_to_unix_time(gms: int, gwk: int) -> float:
    seconds_per_week = 7 * 24 * 60 * 60
    gps_seconds_base = gwk * seconds_per_week

    t_base = Time(gps_seconds_base, format='gps')
    dt = TimeDelta(gms / 1000.0, format='sec')
    t_final = t_base + dt

    return float(t_final.unix)


def read_bin_log_timesync_rtt(bin_log_file: str) -> npt.NDArray[numpy.float64]:
    print(f'Started reading {bin_log_file} log TIMESYNC messages')
    ret: list[tuple[float, float]] = []

    log = mavutil.mavlink_connection(bin_log_file)
    while True:
        msg = log.recv_match()

        if msg is None:
            break

        if msg.get_type() == 'TSYN':
            time_us = msg.TimeUS / 1e6
            rtt = msg.RTT / 1e6
            ret.append((time_us, rtt))

    if len(ret) < 2:
        print(f"{Fore.RED}Didn't find any TSYN in bin log - exiting", flush=True)
        raise MissingDataError('Missing TSYN in BIN log')

    print(f'{Fore.GREEN}Finished reading {bin_log_file} log TIMESYNC messages')
    return numpy.array(ret)


def read_bin_log_gps(bin_log_file: str) -> npt.NDArray[numpy.float64]:
    print(f'Started reading {bin_log_file} log GPS messages')
    ret: list[tuple[float, float]] = []

    log = mavutil.mavlink_connection(bin_log_file)
    while True:
        msg = log.recv_match()

        if msg is None:
            break

        if msg.get_type() == 'GPS':
            hdop = msg.HDop
            nsats = msg.NSats
            if hdop > 2.5 or nsats < 4:
                continue

            time_us = msg.TimeUS / 1e6
            gms = msg.GMS
            gwk = msg.GWk
            gps_synced_unixtime = gps_time_to_unix_time(gms, gwk)

            ret.append((time_us, gps_synced_unixtime))

    if len(ret) < 2:
        print(f"{Fore.RED}Didn't find any GPS time in bin log - exiting", flush=True)
        raise MissingDataError('Missing GPS in BIN log')

    print(f'{Fore.GREEN}Finished reading {bin_log_file} log GPS messages')
    return numpy.array(ret)


def read_tlog(tlog_file: str) -> npt.NDArray[numpy.float64]:
    print(f'Started reading {tlog_file} TIMESYNC messages')
    log = mavutil.mavlink_connection(tlog_file)
    ret: list[tuple[float, float]] = []

    last_time_us = 0
    while True:
        msg = log.recv_match()

        if msg is None:
            break

        if msg.get_type() == 'TIMESYNC':
            if msg.tc1 == 0:
                continue

            if msg.tc1 != 0 and msg.ts1 != 0:
                unix_time = msg.tc1 / 1e9
                time_us = msg.ts1 / 1e9

                # removing pixhawk restarts if there are
                if last_time_us > time_us:
                    print(f'{Fore.YELLOW}Pixhawk restart at(removing) TimeUS: {time_us} - UnixTime: {unix_time}')
                    ret = []

                ret.append((time_us, unix_time))
                last_time_us = time_us

    if len(ret) < 2:
        print(f"{Fore.RED}Didn't find any TIMESYNC in tlog - exiting", flush=True)
        raise MissingDataError('Missing TIMESYNC in tlog')

    print(f'{Fore.GREEN}Finished reading {tlog_file} TIMESYNC messages')
    return numpy.array(ret)


def find_closes_index(
    value: float | int,
    start_index: int,
    sync_array: npt.NDArray[numpy.float64],
    compare_index: int = 0,
) -> int:
    max_idx = len(sync_array) - 2

    while start_index < max_idx and value > sync_array[start_index + 1][compare_index]:
        start_index += 1

    return start_index


def map_rtt_timeus_to_unixtime(
    rtt_times: npt.NDArray[numpy.float64],
    time_sync_times: npt.NDArray[numpy.float64],
) -> npt.NDArray[numpy.float64]:
    idx = 0

    for i, entry in enumerate(rtt_times):
        time_us = entry[0]
        # rtt = entry[1]

        idx = find_closes_index(time_us, idx, time_sync_times)

        interp_func = interp1d(
            (time_sync_times[idx][0], time_sync_times[idx + 1][0]),
            (time_sync_times[idx][1], time_sync_times[idx + 1][1]),
            kind='linear',
            bounds_error=False,
            fill_value='extrapolate',
        )

        interpolated_unix = float(interp_func(time_us))

        rtt_times[i, 0] = interpolated_unix

    return rtt_times


def map_unix_time_to_autopilot_timeus_s(
    unix_time_point: float,
    gcs_time_sync_times: npt.NDArray[numpy.float64],
) -> float:
    time_sync_index = find_closes_index(unix_time_point, 0, gcs_time_sync_times, compare_index=1)

    unix_to_autopilot = interp1d(
        (gcs_time_sync_times[time_sync_index][1], gcs_time_sync_times[time_sync_index + 1][1]),
        (gcs_time_sync_times[time_sync_index][0], gcs_time_sync_times[time_sync_index + 1][0]),
        kind='linear',
        bounds_error=False,
        fill_value='extrapolate',
    )

    autopilot_time_s = float(unix_to_autopilot(unix_time_point))
    return autopilot_time_s


def map_autopilot_timeus_to_gps_unixtime_s(
    autopilot_timeus_s: float,
    gcs_time_sync_times: npt.NDArray[numpy.float64],
    gps_sync_times: npt.NDArray[numpy.float64],
) -> float:
    gps_sync_index = find_closes_index(autopilot_timeus_s, 0, gps_sync_times)
    unix_to_autopilot = interp1d(
        (gps_sync_times[gps_sync_index][0], gcs_time_sync_times[gps_sync_index + 1][0]),
        (gcs_time_sync_times[gps_sync_index][1], gcs_time_sync_times[gps_sync_index + 1][1]),
        kind='linear',
        bounds_error=False,
        fill_value='extrapolate',
    )

    gps_unix_time_s = float(unix_to_autopilot(autopilot_timeus_s))
    return gps_unix_time_s


def sync_mcap_timestamp(
    unixtime_pt_ns: int,
    rtt_times: npt.NDArray[numpy.float64],
    gcs_time_sync_times: npt.NDArray[numpy.float64],
    gps_sync_times: npt.NDArray[numpy.float64],
) -> int:
    unixtime_pt_s = float(unixtime_pt_ns) / 1e9

    rtt_index = find_closes_index(unixtime_pt_s, 0, rtt_times)
    rtt_s = rtt_times[rtt_index][1]

    autopilot_time_s = map_unix_time_to_autopilot_timeus_s(unixtime_pt_s, gcs_time_sync_times)
    corrected_autopilot_time_s = autopilot_time_s - (rtt_s / 2.0)

    gps_unix_time_s = map_autopilot_timeus_to_gps_unixtime_s(
        corrected_autopilot_time_s,
        gcs_time_sync_times,
        gps_sync_times,
    )

    return int(gps_unix_time_s * 1e9)


def sync_mcap(
    mcap_log_file: str,
    rtt_times: npt.NDArray[numpy.float64],
    timesync_times: npt.NDArray[numpy.float64],
    gps_sync_times: npt.NDArray[numpy.float64],
    validate_times: bool,
) -> None:
    # pylint: disable=too-many-locals
    output_file = mcap_log_file.removesuffix('.mcap') + '_synced.mcap'
    with open(mcap_log_file, 'rb') as input_f, open(output_file, 'wb') as output_f:
        reader = make_reader(input_f)
        writer = Writer(output_f, chunk_size=4 * 1024 * 1024, compression=CompressionType.ZSTD)

        header = reader.get_header()
        source_profile = header.profile if header else ''
        source_library = header.library if header else ''
        print(f"\n{Fore.MAGENTA}Detected Source Profile: '{source_profile}' | Library: '{source_library}'")
        writer.start(profile=source_profile, library=source_library)

        schema_id_map = {}
        channel_id_map = {}

        print('Pass 1: Cloning file metadata structures...')
        for schema_id, schema_record in reader.get_summary().schemas.items():
            new_schema_id = writer.register_schema(
                name=schema_record.name,
                encoding=schema_record.encoding,
                data=schema_record.data,
            )
            schema_id_map[schema_id] = new_schema_id

        for channel_id, channel_record in reader.get_summary().channels.items():
            new_schema_id = schema_id_map.get(channel_record.schema_id, 0)

            new_channel_id = writer.register_channel(
                topic=channel_record.topic,
                message_encoding=channel_record.message_encoding,
                schema_id=new_schema_id,
                metadata=channel_record.metadata,
            )
            channel_id_map[channel_id] = new_channel_id

        time_spans_overlapp = False
        print('Pass 2: Rewriting messages with updated timestamps...')
        for _, channel, message in reader.iter_messages():
            old_publish_time = message.publish_time
            new_publish_time = sync_mcap_timestamp(old_publish_time, rtt_times, timesync_times, gps_sync_times)

            if validate_times and not time_spans_overlapp:
                time_spans_overlapp = check_if_time_spans_overlap(timesync_times, old_publish_time / 1e9)
                if time_spans_overlapp:
                    print(f'{Fore.GREEN}{Style.BRIGHT}Found overlapping time section in tlog and mcap time series')

            target_channel_id = channel_id_map[channel.id]

            writer.add_message(
                channel_id=target_channel_id,
                log_time=message.log_time,
                publish_time=new_publish_time,
                data=message.data,
                sequence=message.sequence,
            )

        writer.finish()
        print(f'{Fore.GREEN}{Style.BRIGHT}File writing finished successfully: {output_file}')

        if not time_spans_overlapp and validate_times:
            print(
                f"{Fore.RED}{Style.BRIGHT}Time Sync probably didn't work as expected -"
                + ".tlog times and mcap don't share a common time section",
            )


def check_if_time_spans_overlap(timesync_times: npt.NDArray[numpy.float64], old_publish_time: float) -> bool:
    length = len(timesync_times)
    for i, _ in enumerate(timesync_times):
        if i < length - 2:
            this_unix_time = timesync_times[i][1]
            next_unix_time = timesync_times[i + 1][1]
            if this_unix_time < old_publish_time < next_unix_time:
                return True
    return False


def sync_parallel(bin_path: str, tlog_path: str, mcap_path: str, validate_times: bool = True) -> None:
    rtt_times: npt.NDArray[numpy.float64]
    timesync_times: npt.NDArray[numpy.float64]

    with Pool(processes=3) as pool:
        try:
            rtt_handle = pool.apply_async(read_bin_log_timesync_rtt, args=(bin_path,))
            gps_handle = pool.apply_async(read_bin_log_gps, args=(bin_path,))
            tlog_handle = pool.apply_async(read_tlog, args=(tlog_path,))

            handles = [rtt_handle, gps_handle, tlog_handle]

            while not all(h.ready() for h in handles):
                for h in handles:
                    if h.ready():
                        if h.get() is None:
                            print(
                                f'\n{Fore.RED}{Style.BRIGHT}[CRITICAL] Fast-failing due to missing '
                                + 'tracking packets. Terminating remaining workers...',
                                flush=True,
                            )
                            pool.terminate()
                            pool.join()
                            sys.exit(-1)
                time.sleep(0.1)

            rtt_times = rtt_handle.get()
            timesync_times = tlog_handle.get()
            gps_timesync_times = gps_handle.get()

        except (MissingDataError, Exception) as e:  # pylint: disable=broad-exception-caught
            print(
                f'\n{Fore.RED}{Style.BRIGHT}[CRITICAL] Error or missing '
                + f'packets detected. Terminating remaining background tasks... {e}',
                flush=True,
            )
            pool.terminate()
            pool.join()
            sys.exit(1)

    if rtt_times is None or gps_timesync_times is None or timesync_times is None:
        sys.exit(1)

    rtt_times = map_rtt_timeus_to_unixtime(rtt_times, timesync_times)
    sync_mcap(mcap_path, rtt_times, timesync_times, gps_timesync_times, validate_times)


def main() -> None:
    parser = argparse.ArgumentParser(description='Multiprocess log file synchronizer.')

    parser.add_argument('bin_path', type=str, help='Path to the input .BIN file')
    parser.add_argument('tlog_path', type=str, help='Path to the input .tlog file')

    parser.add_argument(
        'mcap',
        type=str,
        default='logs/log.mcap',
        help='Path to the .mcap file (default: logs/log.mcap)',
    )
    parser.add_argument(
        '--no-overlap-check',
        action='store_true',
        help='Dont check if the time series over of the mcap log and the tlog overlap',
    )

    argcomplete.autocomplete(parser)
    args = parser.parse_args()
    sync_parallel(
        bin_path=args.bin_path,
        tlog_path=args.tlog_path,
        mcap_path=args.mcap,
        validate_times=not args.no_overlap_check,
    )


if __name__ == '__main__':
    init(autoreset=True)

    start = time.time()
    main()
    end = time.time()

    runtime_s = end - start
    print(f'\n{Fore.GREEN}{Style.BRIGHT}Finished syncing logs. Took {runtime_s:.2f}s.')
