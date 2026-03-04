
import subprocess
import time
import os
import sys
from datetime import datetime

HEARTBEAT_FILE = ".heartbeat"
HEARTBEAT_TIMEOUT = 300  # 5 minutes
RESTART_DELAY = 10
ARENA_SCRIPT = "arena.py"

def get_last_heartbeat():
    if not os.path.exists(HEARTBEAT_FILE):
        return 0
    try:
        # Check file modification time
        mtime = os.path.getmtime(HEARTBEAT_FILE)
        return mtime
    except Exception:
        return 0

def main():
    print("=" * 60)
    print(f"[{datetime.now()}] 🛡️ ARENA WATCHDOG STARTED")
    print(f"[{datetime.now()}] Monitoring: {ARENA_SCRIPT}")
    print(f"[{datetime.now()}] Timeout: {HEARTBEAT_TIMEOUT}s | Restart Delay: {RESTART_DELAY}s")
    print("=" * 60)
    
    while True:
        print(f"\n[{datetime.now()}] 🚀 Launching Arena...")
        
        # Reset heartbeat before starting
        if os.path.exists(HEARTBEAT_FILE):
            try:
                os.remove(HEARTBEAT_FILE)
            except Exception:
                pass
            
        # Start arena as a subprocess
        # We pass all arguments received by watchdog.py to arena.py
        process = subprocess.Popen(
            [sys.executable, ARENA_SCRIPT] + sys.argv[1:],
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        
        last_check_time = time.time()
        start_time = time.time()
        
        try:
            while True:
                # 1. Check if process is still running
                retcode = process.poll()
                if retcode is not None:
                    print(f"[{datetime.now()}] ❌ Arena process died with code {retcode}.")
                    break
                    
                # 2. Check heartbeat (but give it some time to start up)
                now = time.time()
                if now - start_time > 60: # Only check heartbeat after 1 minute of uptime
                    if now - last_check_time > 30: # Check every 30 seconds
                        last_heartbeat = get_last_heartbeat()
                        if last_heartbeat > 0:
                            if now - last_heartbeat > HEARTBEAT_TIMEOUT:
                                print(f"[{datetime.now()}] ⚠️ HEARTBEAT TIMEOUT! Arena is frozen ({int(now - last_heartbeat)}s since last beat).")
                                print(f"[{datetime.now()}] 🔪 Terminating frozen process...")
                                process.terminate()
                                try:
                                    process.wait(timeout=10)
                                except subprocess.TimeoutExpired:
                                    process.kill()
                                break
                        else:
                            # Heartbeat file doesn't exist yet or is empty
                            # If it's been more than 5 mins since start and no heartbeat, restart
                            if now - start_time > HEARTBEAT_TIMEOUT:
                                print(f"[{datetime.now()}] ⚠️ No heartbeat file created after {HEARTBEAT_TIMEOUT}s! Restarting...")
                                process.terminate()
                                break
                                
                        last_check_time = now
                
                time.sleep(5)
        except KeyboardInterrupt:
            print(f"\n[{datetime.now()}] 🛑 Watchdog stopped by user.")
            process.terminate()
            try:
                process.wait(timeout=5)
            except:
                process.kill()
            sys.exit(0)
        except Exception as e:
            print(f"[{datetime.now()}] ❗ Watchdog internal error: {e}")
            if process.poll() is None:
                process.terminate()
            
        print(f"[{datetime.now()}] ⏳ Waiting {RESTART_DELAY}s before next restart attempt...")
        time.sleep(RESTART_DELAY)

if __name__ == "__main__":
    main()
