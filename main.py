from mcap.reader import make_reader
from mcap.writer import Writer
from multiprocessing import Pool
from pymavlink import mavutil
from scipy.interpolate import interp1d
import numpy
import numpy.typing as npt

def read_bin_log(bin_log_file:str) -> npt.NDArray[numpy.float64]:
    ret:list[tuple[float, float]] = []

    log = mavutil.mavlink_connection(bin_log_file)
    while True:
        msg = log.recv_match()
        
        if msg is None:
            break

        if msg.get_type() == 'TSYN':
            time_us = msg.TimeUS
            rtt = msg.RTT
            ret.append((time_us, rtt))
            # print(f"Time: {time_us}, RTT: {rtt}")
    
    return numpy.array(ret)

def read_tlog(tlog_file:str) -> npt.NDArray[numpy.float64]:
    log = mavutil.mavlink_connection(tlog_file)
    ret:list[tuple[float, float]] = []

    last_time_us = 0
    while True:
        msg = log.recv_match()
        
        if msg is None:
            break
        
        if msg.get_type() == 'TIMESYNC':
            if msg.tc1 == 0:
                continue
            elif msg.tc1:
                unix_time = msg.tc1
                time_us = msg.ts1
                
                # removing pixhawk restarts if there are
                if last_time_us > time_us:
                    print(F"Pixhawk restart at(removing) TimeUS: {time_us} - UnixTime: {unix_time}")
                    ret = []

                ret.append((time_us, unix_time))
                last_time_us = time_us
                # print(f"TIMESYNC | unix_time(tc1): {unix_time}, time_us(ts1): {time_us}")
    
    return numpy.array(ret)

def find_closes_index(value:float|int, start_index:int, sync_array:npt.NDArray[numpy.float64]) -> int:
    max_idx = len(sync_array) - 2

    while start_index < max_idx and value < sync_array[start_index][0]:
        start_index += 1
    
    return start_index

def map_rtt_timeus_to_unixtime(rtt_times:npt.NDArray[numpy.float64], time_sync_times:npt.NDArray[numpy.float64]) -> npt.NDArray[numpy.float64]:
    idx = 0
    max_idx = len(time_sync_times) - 2

    for i, entry in enumerate(rtt_times):
        time_us = entry[0]
        # rtt = entry[1]

        idx = find_closes_index(time_us, idx, time_sync_times)

        interp_func = interp1d(
            (time_sync_times[idx][0], time_sync_times[idx+1][0]), 
            (time_sync_times[idx][1], time_sync_times[idx+1][1]), 
            kind="linear", 
            bounds_error=False, 
            fill_value="extrapolate"
        )

        interpolated_unix = float(interp_func(time_us))

        rtt_times[i, 0] = interpolated_unix

    return rtt_times

def sync_mcap_timestamp(unixtime_pt_ns:int, rtt_times:npt.NDArray[numpy.float64], time_sync_times:npt.NDArray[numpy.float64]) -> int:
    unixtime_pt_s = float(unixtime_pt_ns) / 1e9



    return int(unixtime_pt_s * 1e9)

def sync_mcap(mcap_log_file:str, rtt_times:npt.NDArray[numpy.float64], time_sync_times:npt.NDArray[numpy.float64]) -> None:
    # Open files for streaming
    output_file = mcap_log_file.removesuffix('.mcap') + '_synced.mcap'
    with open(mcap_log_file, "rb") as input_f, open(output_file, "wb") as output_f:
        reader = make_reader(input_f)
        writer = Writer(output_f)
        
        header = reader.get_header()
        source_profile = header.profile if header else ""
        source_library = header.library if header else ""
        print(f"Detected Source Profile: '{source_profile}' | Library: '{source_library}'")
        writer.start(profile=source_profile, library=source_library)
        
        # Mappings to link input IDs to new output IDs
        schema_id_map = {}
        channel_id_map = {}
        
        print("Pass 1: Cloning file metadata structures...")
        for schema_id, schema_record in reader.get_summary().schemas.items():
            new_schema_id = writer.register_schema(
                name=schema_record.name,
                encoding=schema_record.encoding,
                data=schema_record.data
            )
            schema_id_map[schema_id] = new_schema_id
            
        for channel_id, channel_record in reader.get_summary().channels.items():
            new_schema_id = schema_id_map.get(channel_record.schema_id, 0)
            
            new_channel_id = writer.register_channel(
                topic=channel_record.topic,
                message_encoding=channel_record.message_encoding,
                schema_id=new_schema_id,
                metadata=channel_record.metadata
            )
            channel_id_map[channel_id] = new_channel_id


        # time_offset_ns = 60 * 60 * 1_000_000_000 
        print("Pass 2: Rewriting messages with updated timestamps...")
        for _, channel, message in reader.iter_messages():
            new_publish_time = sync_mcap_timestamp(message.publish_time, rtt_times, time_sync_times)
            
            target_channel_id = channel_id_map[channel.id]

            writer.add_message(
                channel_id=target_channel_id,
                log_time=message.log_time,
                publish_time=new_publish_time,
                data=message.data,
                sequence=message.sequence
            )
            
        writer.finish()
        print(f"File writing finished successfully: {output_file}")

def multiprocess() -> None:
    rtt_times:npt.NDArray[numpy.float64]
    timesync_times:npt.NDArray[numpy.float64]

    with Pool(processes=3) as pool:
        print("reading bin log")
        read_bin_handle = pool.apply_async(read_bin_log, args=('logs/00000018.BIN',))
        print("reading tlog")
        read_tlog_handle = pool.apply_async(read_tlog, args=('logs/2026-05-23 19-06-38.tlog',))

        rtt_times = read_bin_handle.get()
        timesync_times = read_tlog_handle.get()

        print(rtt_times, end='\n\n\n')
        print(timesync_times)

    rtt_times = map_rtt_timeus_to_unixtime(rtt_times, timesync_times)
    print(rtt_times, end='\n\n\n')
    sync_mcap("logs/log.mcap", rtt_times, timesync_times)

if __name__ == "__main__":
    multiprocess()
