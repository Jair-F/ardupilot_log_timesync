import threading
from multiprocessing import Pool, Process
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
    