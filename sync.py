#!/bin/python3
import argparse
import sys
import time
from multiprocessing.pool import Pool

import argcomplete
import numpy
import numpy.typing as npt
from astropy.time import Time, TimeDelta
from colorama import Fore, Style, init
from mcap.reader import make_reader
from mcap.writer import CompressionType, Writer
from pymavlink import mavutil
from scipy.interpolate import interp1d

SECONDS_PER_WEEK = 7 * 24 * 60 * 60
MIN_REQUIRED_SAMPLES = 2


class MissingDataError(Exception):
    pass


def gps_time_to_unix_time(gms: int, gwk: int) -> float:
    gps_week_start_seconds = gwk * SECONDS_PER_WEEK

    week_start = Time(gps_week_start_seconds, format='gps')
    offset = TimeDelta(gms / 1000.0, format='sec')
    absolute_time = week_start + offset

    return float(absolute_time.unix)


def _require_min_samples(samples: list[tuple[float, float]], log_file: str, message_type: str) -> None:
    if len(samples) < MIN_REQUIRED_SAMPLES:
        print(f"{Fore.RED}Didn't find any {message_type} in {log_file} - exiting", flush=True)
        raise MissingDataError(f'Missing {message_type} in {log_file}')


def read_bin_log_timesync_rtt(bin_log_file: str) -> npt.NDArray[numpy.float64]:
    print(f'Started reading {bin_log_file} log TIMESYNC messages')
    samples: list[tuple[float, float]] = []

    log = mavutil.mavlink_connection(bin_log_file)
    while True:
        msg = log.recv_match()
        if msg is None:
            break

        if msg.get_type() == 'TSYN':
            time_us = msg.TimeUS / 1e6
            rtt = msg.RTT / 1e6
            samples.append((time_us, rtt))

    _require_min_samples(samples, bin_log_file, 'TSYN')

    print(f'{Fore.GREEN}Finished reading {bin_log_file} log TIMESYNC messages')
    return numpy.array(samples)


def read_bin_log_gps(bin_log_file: str) -> npt.NDArray[numpy.float64]:
    print(f'Started reading {bin_log_file} log GPS messages')
    samples: list[tuple[float, float]] = []

    log = mavutil.mavlink_connection(bin_log_file)
    while True:
        msg = log.recv_match()
        if msg is None:
            break

        if msg.get_type() != 'GPS':
            continue

        if msg.HDop > 2.5 or msg.NSats < 4:
            continue

        time_us = msg.TimeUS / 1e6
        gps_synced_unixtime = gps_time_to_unix_time(msg.GMS, msg.GWk)
        samples.append((time_us, gps_synced_unixtime))

    _require_min_samples(samples, bin_log_file, 'GPS time')

    print(f'{Fore.GREEN}Finished reading {bin_log_file} log GPS messages')
    return numpy.array(samples)


def read_tlog(tlog_file: str) -> npt.NDArray[numpy.float64]:
    print(f'Started reading {tlog_file} TIMESYNC messages')
    log = mavutil.mavlink_connection(tlog_file)
    samples: list[tuple[float, float]] = []

    last_time_us = 0
    while True:
        msg = log.recv_match()
        if msg is None:
            break

        if msg.get_type() != 'TIMESYNC':
            continue

        if msg.tc1 == 0 or msg.ts1 == 0:
            continue

        unix_time = msg.tc1 / 1e9
        time_us = msg.ts1 / 1e9

        # removing pixhawk restarts if there are
        if last_time_us > time_us:
            print(f'{Fore.YELLOW}Pixhawk restart at(removing) TimeUS: {time_us} - UnixTime: {unix_time}')
            samples = []

        samples.append((time_us, unix_time))
        last_time_us = time_us

    _require_min_samples(samples, tlog_file, 'TIMESYNC')

    print(f'{Fore.GREEN}Finished reading {tlog_file} TIMESYNC messages')
    return numpy.array(samples)


def find_closest_index(
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
    search_index = 0

    for row_index, (time_us, _rtt) in enumerate(rtt_times):
        search_index = find_closest_index(time_us, search_index, time_sync_times)

        interp_func = interp1d(
            (time_sync_times[search_index][0], time_sync_times[search_index + 1][0]),
            (time_sync_times[search_index][1], time_sync_times[search_index + 1][1]),
            kind='linear',
            bounds_error=False,
            fill_value='extrapolate',
        )

        rtt_times[row_index, 0] = float(interp_func(time_us))

    return rtt_times


def map_unix_time_to_autopilot_timeus_s(
    unix_time_point: float,
    gcs_time_sync_times: npt.NDArray[numpy.float64],
) -> float:
    sync_index = find_closest_index(unix_time_point, 0, gcs_time_sync_times, compare_index=1)

    unix_to_autopilot = interp1d(
        (gcs_time_sync_times[sync_index][1], gcs_time_sync_times[sync_index + 1][1]),
        (gcs_time_sync_times[sync_index][0], gcs_time_sync_times[sync_index + 1][0]),
        kind='linear',
        bounds_error=False,
        fill_value='extrapolate',
    )

    return float(unix_to_autopilot(unix_time_point))


def map_autopilot_timeus_to_gps_unixtime_s(
    autopilot_timeus_s: float,
    gcs_time_sync_times: npt.NDArray[numpy.float64],
    gps_sync_times: npt.NDArray[numpy.float64],
) -> float:
    gps_sync_index = find_closest_index(autopilot_timeus_s, 0, gps_sync_times)
    unix_to_autopilot = interp1d(
        (gps_sync_times[gps_sync_index][0], gcs_time_sync_times[gps_sync_index + 1][0]),
        (gcs_time_sync_times[gps_sync_index][1], gcs_time_sync_times[gps_sync_index + 1][1]),
        kind='linear',
        bounds_error=False,
        fill_value='extrapolate',
    )

    return float(unix_to_autopilot(autopilot_timeus_s))


def sync_mcap_timestamp(
    unixtime_pt_ns: int,
    rtt_times: npt.NDArray[numpy.float64],
    gcs_time_sync_times: npt.NDArray[numpy.float64],
    gps_sync_times: npt.NDArray[numpy.float64],
) -> int:
    # pylint: disable=too-many-locals
    unixtime_pt_s = float(unixtime_pt_ns) / 1e9

    rtt_index = find_closest_index(unixtime_pt_s, 0, rtt_times)
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

        time_spans_overlap = False
        print('Pass 2: Rewriting messages with updated timestamps...')
        for _, channel, message in reader.iter_messages():
            old_publish_time = message.publish_time
            new_publish_time = sync_mcap_timestamp(old_publish_time, rtt_times, timesync_times, gps_sync_times)

            if validate_times and not time_spans_overlap:
                time_spans_overlap = check_if_time_spans_overlap(timesync_times, old_publish_time / 1e9)
                if time_spans_overlap:
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

        if not time_spans_overlap and validate_times:
            print(
                f"{Fore.RED}{Style.BRIGHT}[WARNING] Time Sync probably didn't work as expected - "
                ".tlog times and mcap don't share a common time section",
            )


def check_if_time_spans_overlap(timesync_times: npt.NDArray[numpy.float64], old_publish_time: float) -> bool:
    if len(timesync_times) < 2:
        return False
    low_bounds = timesync_times[:-2, 1]
    high_bounds = timesync_times[1:-1, 1]
    return bool(numpy.any((low_bounds < old_publish_time) & (old_publish_time < high_bounds)))


def _fail_and_terminate(pool: Pool, message: str, exit_code: int) -> None:
    print(message, flush=True)
    pool.terminate()
    pool.join()
    sys.exit(exit_code)


def sync_parallel(bin_path: str, tlog_path: str, mcap_path: str, validate_times: bool = True) -> None:
    with Pool(processes=3) as pool:
        try:
            rtt_handle = pool.apply_async(read_bin_log_timesync_rtt, args=(bin_path,))
            time.sleep(0.02)
            gps_handle = pool.apply_async(read_bin_log_gps, args=(bin_path,))
            time.sleep(0.02)
            tlog_handle = pool.apply_async(read_tlog, args=(tlog_path,))

            handles = [rtt_handle, gps_handle, tlog_handle]

            while not all(h.ready() for h in handles):
                for h in handles:
                    if h.ready() and h.get() is None:
                        _fail_and_terminate(
                            pool,
                            f'\n{Fore.RED}{Style.BRIGHT}[CRITICAL] Fast-failing due to missing '
                            'tracking packets. Terminating remaining workers...',
                            -1,
                        )
                time.sleep(0.1)

            rtt_times = rtt_handle.get()
            timesync_times = tlog_handle.get()
            gps_timesync_times = gps_handle.get()

        except (MissingDataError, Exception) as e:  # pylint: disable=broad-exception-caught
            _fail_and_terminate(
                pool,
                f'\n{Fore.RED}{Style.BRIGHT}[CRITICAL] Error or missing '
                f'packets detected. Terminating remaining background tasks... {e}',
                1,
            )

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
        help="Don't check if the time series over of the mcap log and the tlog overlap",
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

    print(f'\n{Fore.GREEN}{Style.BRIGHT}Finished syncing logs. Took {end - start:.2f}s.')
