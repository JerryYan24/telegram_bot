import lcm
from train_msgs import TrainStatus
from threading import Thread
import time

class TrainingMonitor:
    def __init__(self):
        self.lc = lcm.LCM()
        self.latest_status = None
        self._start_listener()

    def _start_listener(self):
        def handler(channel, data):
            msg = TrainStatus.decode(data)
            self.latest_status = msg

        self.lc.subscribe("TRAIN_STATUS", handler)
        Thread(target=self._run_lcm_loop, daemon=True).start()

    def _run_lcm_loop(self):
        while True:
            self.lc.handle()
            time.sleep(0.1)

    def get_status(self):
        if self.latest_status is None:
            return "No training information available yet."
        
        status = self.latest_status
        return f"""🤖 Training Status:
📊 Epoch: {status.epoch}
📈 Step: {status.step}
📉 Loss: {status.loss:.4f}
🎯 Reward: {status.reward:.4f}
📚 Learning Rate: {status.lr:.6f}
⏰ Last Update: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(status.timestamp/1000))}""" 