import socket
import datetime
import psutil
import GPUtil

class SystemMonitor:
    def __init__(self):
        self.hostname = socket.gethostname()
        self.ip = socket.gethostbyname(self.hostname)

    def get_cpu_temperature(self):
        try:
            # Try to get CPU temperature from psutil
            temps = psutil.sensors_temperatures()
            if 'coretemp' in temps:
                # Get average temperature of all CPU cores
                core_temps = temps['coretemp']
                avg_temp = sum(temp.current for temp in core_temps) / len(core_temps)
                return f"{avg_temp:.1f}°C"
            elif 'k10temp' in temps:  # For AMD processors
                return f"{temps['k10temp'][0].current:.1f}°C"
            else:
                return "N/A"
        except Exception as e:
            print(f"Error getting CPU temperature: {str(e)}")
            return "N/A"

    def get_gpu_info(self):
        gpu_info = ""
        try:
            print("Attempting to get GPU information...")
            gpus = GPUtil.getGPUs()
            print(f"Found {len(gpus)} GPUs")
            if gpus:
                gpu_info = "\n🎮 GPU Information:\n"
                for gpu in gpus:
                    print(f"Processing GPU {gpu.id}: {gpu.name}")
                    gpu_info += (
                        f"GPU {gpu.id}: {gpu.name}\n"
                        f"Load: {gpu.load*100:.1f}%\n"
                        f"Memory: {gpu.memoryUsed}MB / {gpu.memoryTotal}MB\n"
                        f"Temperature: {gpu.temperature}°C\n"
                    )
            else:
                print("No GPUs found")
                gpu_info = "\n🎮 GPU: No GPUs detected"
        except Exception as e:
            print(f"Error getting GPU information: {str(e)}")
            gpu_info = "\n🎮 GPU: Not available or not supported"
        return gpu_info

    def get_status(self):
        uptime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cpu = psutil.cpu_percent()
        cpu_temp = self.get_cpu_temperature()
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        gpu_info = self.get_gpu_info()

        status_message = (
            f"🖥 Host: {self.hostname} ({self.ip})\n"
            f"📅 Time: {uptime}\n"
            f"🔥 CPU: {cpu}% (Temp: {cpu_temp})\n"
            f"🧠 Mem: {mem.percent}% ({mem.used // 1024**2}MB / {mem.total // 1024**2}MB)\n"
            f"💾 Disk: {disk.percent}% ({disk.used // 1024**3}GB / {disk.total // 1024**3}GB)"
            f"{gpu_info}"
        )
        print("Final status message:", status_message)
        return status_message 