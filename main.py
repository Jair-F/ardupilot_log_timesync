from mcap.reader import make_reader
from mcap.writer import Writer
from multiprocessing import Pool
from pymavlink import mavutil

def read_bin_log(bin_log_file:str) -> None:
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
    
    return ret

def read_tlog(tlog_file:str) -> None:
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
    
    return ret

def sync_mcap(mcap_log_file:str, rtt_times:list[tuple[float, float]], time_sync_times:list[tuple[float, float]]) -> None:
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


        time_offset_ns = 60 * 60 * 1_000_000_000 
        print("Pass 2: Rewriting messages with updated timestamps...")
        for _, channel, message in reader.iter_messages():
            new_publish_time = message.publish_time + time_offset_ns
            
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
    with Pool(processes=3) as pool:
        print("reading bin log")
        read_bin_handle = pool.apply_async(read_bin_log, args=('logs/00000018.BIN',))
        print("reading tlog")
        read_tlog_handle = pool.apply_async(read_tlog, args=('logs/2026-05-23 19-06-38.tlog',))

        ret_bin = read_bin_handle.get()
        ret_tlog = read_tlog_handle.get()

        print(ret_bin, end='\n\n\n')
        print(ret_tlog)

if __name__ == "__main__":
    multiprocess()
    