#!/bin/python3

import argparse
import sys
import time
from collections.abc import Iterator
from multiprocessing.pool import ApplyResult, Pool
from typing import Any

import argcomplete
import numpy
import numpy.typing as npt
from astropy.time import Time, TimeDelta
from colorama import Fore, Style, init
from mcap.reader import McapReader, make_reader
from mcap.writer import CompressionType, Writer
from pymavlink import mavutil
from scipy.interpolate import interp1d

SECONDS_PER_WEEK = 7 * 24 * 60 * 60
MIN_REQUIRED_SAMPLES = 2
MAX_RTT_SECONDS = 5.0

is_dst = time.localtime().tm_isdst > 0
LOCAL_OFFSET_SECONDS = -time.altzone if is_dst else -time.timezone
DEFAULT_TZ_OFFSET_HOURS = 0
CURRENT_TZ_NAME = time.tzname[1 if is_dst else 0]


class MissingDataError(Exception):
    pass


def gps_time_to_unix_time(gms: int, gwk: int, offset_hours: float = 0.0) -> float:
    gps_week_start_seconds = gwk * SECONDS_PER_WEEK

    week_start = Time(gps_week_start_seconds, format='gps')
    offset = TimeDelta(gms / 1000.0, format='sec')
    absolute_time = week_start + offset

    utc_unix = float(absolute_time.unix)
    return utc_unix + (offset_hours * 3600.0)


def _require_min_samples(samples: list[tuple[float, float]], log_file: str, message_type: str) -> None:
    if len(samples) < MIN_REQUIRED_SAMPLES:
        print(f"{Fore.RED}Didn't find any {message_type} in {log_file} - exiting", flush=True)
        raise MissingDataError(f'Missing {message_type} in {log_file}')


def _iter_mavlink_messages(log_file: str, message_type: str) -> Iterator[mavutil.mavlink.MAVLink_message]:
    log = mavutil.mavlink_connection(log_file)
    while True:
        msg = log.recv_match()
        if msg is None:
            return
        if msg.get_type() == message_type:
            yield msg


def read_bin_log_timesync_rtt(bin_log_file: str) -> npt.NDArray[numpy.float64]:
    print(f'Started reading {bin_log_file} log TIMESYNC messages')

    samples = [(msg.TimeUS / 1e6, msg.RTT / 1e6) for msg in _iter_mavlink_messages(bin_log_file, 'TSYN')]
    _require_min_samples(samples, bin_log_file, 'TSYN')

    print(f'{Fore.GREEN}Finished reading {bin_log_file} log TIMESYNC messages')
    return numpy.array(samples)


def read_bin_log_gps(bin_log_file: str, offset_hours: float = 0.0) -> npt.NDArray[numpy.float64]:
    print(f'Started reading {bin_log_file} log GPS messages')

    samples: list[tuple[float, float]] = []
    for msg in _iter_mavlink_messages(bin_log_file, 'GPS'):
        if msg.HDop > 2.5 or msg.NSats < 4:
            continue
        if msg.GMS == 0 or msg.GWk == 0:
            continue

        time_us = msg.TimeUS / 1e6
        gps_synced_unixtime = gps_time_to_unix_time(msg.GMS, msg.GWk, offset_hours)
        samples.append((time_us, gps_synced_unixtime))

    _require_min_samples(samples, bin_log_file, 'GPS time')

    print(f'{Fore.GREEN}Finished reading {bin_log_file} log GPS messages')
    return numpy.array(samples)


def read_tlog(tlog_file: str) -> npt.NDArray[numpy.float64]:
    print(f'Started reading {tlog_file} TIMESYNC messages')

    samples: list[tuple[float, float]] = []
    last_time_us = 0
    for msg in _iter_mavlink_messages(tlog_file, 'TIMESYNC'):
        if msg.tc1 == 0 or msg.ts1 == 0:
            continue

        unix_time = msg._timestamp  # pylint: disable=protected-access
        time_us = msg.ts1 / 1e9

        # removing pixhawk restarts if there are
        if round(last_time_us) > round(time_us):
            print(f'{Fore.YELLOW}Pixhawk restart at(removing) TimeUS: {last_time_us} - UnixTime: {unix_time}')
            samples = []

        samples.append((time_us, unix_time))
        last_time_us = time_us

    _require_min_samples(samples, tlog_file, 'TIMESYNC')

    print(f'{Fore.GREEN}Finished reading {tlog_file} TIMESYNC messages')
    return numpy.array(samples)


def read_mcap_time_bounds(mcap_file: str) -> tuple[float, float]:
    print(f'Started reading {mcap_file} time bounds')

    with open(mcap_file, 'rb') as f:
        reader = make_reader(f)
        summary = reader.get_summary()

        if summary is not None and summary.statistics is not None:
            stats = summary.statistics
            if stats.message_start_time and stats.message_end_time:
                start_s = stats.message_start_time / 1e9
                end_s = stats.message_end_time / 1e9
                print(f'{Fore.GREEN}Finished reading {mcap_file} time bounds (from summary)')
                return start_s, end_s

        print(f'{Fore.YELLOW}No summary statistics found in {mcap_file}, falling back to full scan')

    first_time_ns = None
    last_time_ns = None
    with open(mcap_file, 'rb') as f:
        reader = make_reader(f)
        for _, _, message in reader.iter_messages():
            if first_time_ns is None:
                first_time_ns = message.log_time
            last_time_ns = message.log_time

    if first_time_ns is None or last_time_ns is None:
        raise MissingDataError(f'No messages found in {mcap_file}')

    print(f'{Fore.GREEN}Finished reading {mcap_file} time bounds (from full scan)')
    return first_time_ns / 1e9, last_time_ns / 1e9


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


class SequentialLookup:
    def __init__(self, sync_array: npt.NDArray[numpy.float64], compare_index: int = 0) -> None:
        self.array = sync_array
        self._compare_index = compare_index
        self._value_index = 1 - compare_index
        self._cursor = 0

    def index_at(self, value: float) -> int:
        self._cursor = find_closest_index(value, self._cursor, self.array, self._compare_index)
        return self._cursor

    def interpolate(self, value: float) -> float:
        idx = self.index_at(value)
        x = (self.array[idx][self._compare_index], self.array[idx + 1][self._compare_index])
        y = (self.array[idx][self._value_index], self.array[idx + 1][self._value_index])

        interp_func = interp1d(x, y, kind='linear', bounds_error=False, fill_value='extrapolate')
        return float(interp_func(value))


def map_rtt_timeus_to_unixtime(
    rtt_times: npt.NDArray[numpy.float64],
    time_sync_times: npt.NDArray[numpy.float64],
) -> npt.NDArray[numpy.float64]:
    lookup = SequentialLookup(time_sync_times, compare_index=0)

    for row_index, (time_us, _rtt) in enumerate(rtt_times):
        rtt_times[row_index, 0] = lookup.interpolate(time_us)

    return rtt_times


def sync_mcap_timestamp(
    unixtime_pt_ns: int,
    rtt_lookup: SequentialLookup,
    unix_to_autopilot_lookup: SequentialLookup,
    autopilot_to_gps_lookup: SequentialLookup,
) -> int:
    unixtime_pt_s = float(unixtime_pt_ns) / 1e9

    rtt_index = rtt_lookup.index_at(unixtime_pt_s)
    rtt_s = rtt_lookup.array[rtt_index][1]

    if rtt_s >= MAX_RTT_SECONDS:
        rtt_col = rtt_lookup.array[:, 1]
        rtt_s = rtt_col[rtt_col < MAX_RTT_SECONDS].mean()

    autopilot_time_s = unix_to_autopilot_lookup.interpolate(unixtime_pt_s)
    corrected_autopilot_time_s = autopilot_time_s - (rtt_s / 2.0)

    gps_unix_time_s = autopilot_to_gps_lookup.interpolate(corrected_autopilot_time_s)

    new_time_ns = int(gps_unix_time_s * 1e9)
    if not 0 <= new_time_ns <= 2**64 - 1:
        raise ValueError(
            f'Computed out-of-range publish_time {new_time_ns} for input {unixtime_pt_ns} - '
            'check that the .mcap and .tlog time ranges overlap (or are close enough to extrapolate safely)',
        )
    return new_time_ns


def _clone_schemas_and_channels(reader: McapReader, writer: Writer) -> tuple[dict[int, int], dict[int, int]]:
    summary = reader.get_summary()

    schema_id_map = {}
    for schema_id, schema_record in summary.schemas.items():
        schema_id_map[schema_id] = writer.register_schema(
            name=schema_record.name,
            encoding=schema_record.encoding,
            data=schema_record.data,
        )

    channel_id_map = {}
    for channel_id, channel_record in summary.channels.items():
        new_schema_id = schema_id_map.get(channel_record.schema_id, 0)
        channel_id_map[channel_id] = writer.register_channel(
            topic=channel_record.topic,
            message_encoding=channel_record.message_encoding,
            schema_id=new_schema_id,
            metadata=channel_record.metadata,
        )

    return schema_id_map, channel_id_map


def sync_mcap(
    mcap_log_file: str,
    rtt_times: npt.NDArray[numpy.float64],
    timesync_times: npt.NDArray[numpy.float64],
    gps_sync_times: npt.NDArray[numpy.float64],
) -> None:
    # pylint: disable=too-many-locals
    output_file = mcap_log_file.removesuffix('.mcap') + '_synced.mcap'

    rtt_lookup = SequentialLookup(rtt_times, compare_index=0)
    unix_to_autopilot_lookup = SequentialLookup(timesync_times, compare_index=1)
    autopilot_to_gps_lookup = SequentialLookup(gps_sync_times, compare_index=0)

    with open(mcap_log_file, 'rb') as input_f, open(output_file, 'wb') as output_f:
        reader = make_reader(input_f)
        writer = Writer(output_f, chunk_size=4 * 1024 * 1024, compression=CompressionType.ZSTD)

        header = reader.get_header()
        source_profile = header.profile if header else ''
        source_library = header.library if header else ''
        print(f"\n{Fore.MAGENTA}Detected Source Profile: '{source_profile}' | Library: '{source_library}'")
        writer.start(profile=source_profile, library=source_library)

        print('Pass 1: Cloning file metadata structures...')
        _, channel_id_map = _clone_schemas_and_channels(reader, writer)

        print('Pass 2: Rewriting messages with updated timestamps...')
        for _, channel, message in reader.iter_messages():
            new_publish_time = sync_mcap_timestamp(
                message.publish_time,
                rtt_lookup,
                unix_to_autopilot_lookup,
                autopilot_to_gps_lookup,
            )
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


def check_if_time_spans_overlap(
    timesync_times: npt.NDArray[numpy.float64],
    mcap_start_s: float,
    mcap_end_s: float,
) -> bool:
    if len(timesync_times) < 2:
        return False

    tlog_start_s = timesync_times[0][1]
    tlog_end_s = timesync_times[-1][1]

    return bool(mcap_start_s <= tlog_end_s and mcap_end_s >= tlog_start_s)


def validate_times_overlap(
    timesync_times: npt.NDArray[numpy.float64],
    mcap_start_s: float,
    mcap_end_s: float,
) -> None:
    if check_if_time_spans_overlap(timesync_times, mcap_start_s, mcap_end_s):
        print(f'{Fore.GREEN}{Style.BRIGHT}Found overlapping time section in tlog and mcap time series')
        return

    tlog_start_s, tlog_end_s = timesync_times[0][1], timesync_times[-1][1]
    print(
        f"{Fore.RED}{Style.BRIGHT}[WARNING] Time Sync probably didn't work as expected - "
        f'.tlog ({tlog_start_s:.1f} - {tlog_end_s:.1f}) and mcap ({mcap_start_s:.1f} - {mcap_end_s:.1f}) '
        "don't share a common time section",
    )


def _fail_and_terminate(pool: Pool, message: str, exit_code: int) -> None:
    """Print `message`, forcibly stop all pool workers, and exit the process."""
    print(message, flush=True)
    pool.terminate()
    pool.join()
    sys.exit(exit_code)


def _wait_for_handles(pool: Pool, handles: list[ApplyResult[Any]]) -> None:
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


def sync_parallel(
    bin_path: str,
    tlog_path: str,
    mcap_path: str,
    validate_times: bool = True,
    offset_hours: float = 0.0,
) -> None:
    with Pool(processes=3) as pool:
        try:
            rtt_handle = pool.apply_async(read_bin_log_timesync_rtt, args=(bin_path,))
            time.sleep(0.02)
            gps_handle = pool.apply_async(read_bin_log_gps, args=(bin_path, offset_hours))
            time.sleep(0.02)
            tlog_handle = pool.apply_async(read_tlog, args=(tlog_path,))

            _wait_for_handles(pool, [rtt_handle, gps_handle, tlog_handle])

            rtt_times = rtt_handle.get()
            timesync_times = tlog_handle.get()
            gps_timesync_times = gps_handle.get()

        except Exception as e:  # pylint: disable=broad-exception-caught
            _fail_and_terminate(
                pool,
                f'\n{Fore.RED}{Style.BRIGHT}[CRITICAL] Error or missing '
                f'packets detected. Terminating remaining background tasks... {e}',
                1,
            )

    if rtt_times is None or gps_timesync_times is None or timesync_times is None:
        sys.exit(1)

    if validate_times:
        mcap_start_s, mcap_end_s = read_mcap_time_bounds(mcap_path)
        validate_times_overlap(timesync_times, mcap_start_s, mcap_end_s)

    rtt_times = map_rtt_timeus_to_unixtime(rtt_times, timesync_times)
    sync_mcap(mcap_path, rtt_times, timesync_times, gps_timesync_times)


def main() -> None:
    print(
        f'{Fore.CYAN}System Current Timezone: {CURRENT_TZ_NAME} | '
        + f'Default Offset: {DEFAULT_TZ_OFFSET_HOURS} hours{Style.RESET_ALL}\n',
    )

    parser = argparse.ArgumentParser(
        description=(
            'Multiprocess log file synchronizer.\n'
            f'System Current Timezone: {CURRENT_TZ_NAME} | Default Offset: {DEFAULT_TZ_OFFSET_HOURS} hours'
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )

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
    parser.add_argument(
        '--tz-offset',
        type=float,
        default=DEFAULT_TZ_OFFSET_HOURS,
        help=f'Timezone offset in hours to apply (default: {DEFAULT_TZ_OFFSET_HOURS} for {CURRENT_TZ_NAME})',
    )

    argcomplete.autocomplete(parser)
    args = parser.parse_args()

    sync_parallel(
        bin_path=args.bin_path,
        tlog_path=args.tlog_path,
        mcap_path=args.mcap,
        validate_times=not args.no_overlap_check,
        offset_hours=args.tz_offset,
    )


if __name__ == '__main__':
    init(autoreset=True)

    start = time.time()
    main()
    end = time.time()

    print(f'\n{Fore.GREEN}{Style.BRIGHT}Finished syncing logs. Took {end - start:.2f}s.')
