from pymavlink import mavutil

def read_bin_log() -> None:
    log_file = "logs/ArduPlane-GpsSensorPreArmEAHRS-00000140.BIN"
    log = mavutil.mavlink_connection(log_file)
    while True:
        msg = log.recv_match()
        
        if msg is None:
            break
            
        if msg.get_type() == 'ATT':
            print(f"Time: {msg.TimeUS}, Roll: {msg.Roll}, Pitch: {msg.Pitch}")
        if msg.get_type() == 'TSYN':
            time_us = msg.TimeUS
            rtt = msg.RTT

def read_tlog() -> None:
    tlog_file = "logs/ArduPlane-test.tlog"
    log = mavutil.mavlink_connection(tlog_file)

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

        if msg.get_type() == 'HEARTBEAT':
            print(f"HEARTBEAT | Type: {msg.type}, Autopilot: {msg.autopilot}, Mode: {msg.custom_mode}, Status: {msg.system_status}")

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

if __name__ == "__main__":
    print("reading bin log")
    read_bin_log()
    print("reading tlog")
    read_tlog()