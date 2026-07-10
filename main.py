import threading
from multiprocessing import Pool, Process
from pymavlink import mavutil

def read_bin_log() -> None:
    ret:list[tuple[float, float]] = []

    log_file = "logs/00000018.BIN"
    log = mavutil.mavlink_connection(log_file)
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

def read_tlog() -> None:
    tlog_file = "logs/2026-05-23 19-06-38.tlog"
    log = mavutil.mavlink_connection(tlog_file)
    ret:list[tuple[float, float]] = []

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
                ret.append((time_us, unix_time))
                # print(f"TIMESYNC | unix_time(tc1): {unix_time}, time_us(ts1): {time_us}")

        # if msg.get_type() == 'HEARTBEAT':
        #     print(f"HEARTBEAT | Type: {msg.type}, Autopilot: {msg.autopilot}, Mode: {msg.custom_mode}, Status: {msg.system_status}")
    
    return ret

def remove_pix_restarts(time_sync: list[float, float]) -> None:
    removed_reboot = True

    while removed_reboot == True:
        last_time_us = 0
        removed_reboot = False
        for i in range(len(time_sync)):
            time_us = time_sync[i][0]
            
            if time_us < last_time_us:
                removed_reboot = True
                time_sync = time_sync[i:]
                break
            last_time_us = time_us
    
    return time_sync

def multithread() -> None:
    threads:list[threading.Thread] = []
    print("reading bin log")
    t = threading.Thread(target=read_bin_log)
    t.start()
    threads.append(t)
    print("reading tlog")
    t = threading.Thread(target=read_tlog)
    t.start()
    threads.append(t)

    for t in threads:
        t.join()

def singlethread() -> None:
    print("reading bin log")
    read_bin_log()
    print("reading tlog")
    read_tlog()

def multiprocess() -> None:
    with Pool(processes=3) as pool:
        print("reading bin log")
        read_bin_handle = pool.apply_async(read_bin_log)
        print("reading tlog")
        read_tlog_handle = pool.apply_async(read_tlog)

        ret_bin = read_bin_handle.get()
        ret_tlog = read_tlog_handle.get()

        print(ret_bin)
        print(ret_tlog)

if __name__ == "__main__":
    multiprocess()
    