import wandb
import time
from threading import Thread, Lock
import os
import pandas as pd

# --- CONFIGURATION ---
WANDB_API_KEY = "95feee2dee9755a00180138ba1236c5d2dad5fec"
ENTITY = "jerryyan24-uc-san-diego"
PROJECT = "gsworld"

class TrainingMonitor:
    def __init__(self, project_name=PROJECT, entity=ENTITY):
        self.project_name = project_name
        self.entity = entity
        self.latest_status = None
        self.status_lock = Lock()
        
        # Login to wandb
        try:
            wandb.login(key=WANDB_API_KEY)
            print(f"Successfully logged in to wandb as {self.entity}")
        except Exception as e:
            print(f"Error logging in to wandb: {str(e)}")
            
        self._start_listener()

    def _start_listener(self):
        """Start a thread to listen for wandb updates"""
        def update_loop():
            try:
                # Initialize wandb API
                api = wandb.Api()
                
                # Get the most recent run
                project_path = f"{self.entity}/{self.project_name}" if self.entity else self.project_name
                try:
                    runs = api.runs(project_path, order="-created_at", per_page=1)
                    if not runs:
                        print(f"No runs found in wandb project: {project_path}")
                        with self.status_lock:
                            self.latest_status = {
                                'epoch': 0,
                                'step': 0,
                                'loss': 0.0,
                                'reward': 0.0,
                                'lr': 0.0,
                                'timestamp': int(time.time() * 1000),
                                'status': f'No runs found in project: {project_path}. Please start a training run.'
                            }
                        return
                    
                    latest_run = runs[0]
                    print(f"Found run: {latest_run.id} ({latest_run.name})")
                    
                    while True:
                        try:
                            # Get the latest history
                            history = latest_run.history(keys=["epoch", "step", "loss", "reward", "learning_rate"])
                            
                            # Convert to DataFrame if it's a list
                            if isinstance(history, list):
                                if not history:  # If list is empty
                                    # Don't raise an error, just update with waiting status
                                    with self.status_lock:
                                        self.latest_status = {
                                            'epoch': 0,
                                            'step': 0,
                                            'loss': 0.0,
                                            'reward': 0.0,
                                            'lr': 0.0,
                                            'timestamp': int(time.time() * 1000),
                                            'status': f'Waiting for data in run: {latest_run.name}'
                                        }
                                    time.sleep(5)  # Check less frequently when waiting
                                    continue
                                history = pd.DataFrame(history)
                            
                            if len(history) == 0:
                                # Don't raise an error, just update with waiting status
                                with self.status_lock:
                                    self.latest_status = {
                                        'epoch': 0,
                                        'step': 0,
                                        'loss': 0.0,
                                        'reward': 0.0,
                                        'lr': 0.0,
                                        'timestamp': int(time.time() * 1000),
                                        'status': f'Waiting for data in run: {latest_run.name}'
                                    }
                                time.sleep(5)  # Check less frequently when waiting
                                continue
                                
                            latest = history.iloc[-1]
                            with self.status_lock:
                                self.latest_status = {
                                    'epoch': int(latest.get('epoch', 0)),
                                    'step': int(latest.get('step', 0)),
                                    'loss': float(latest.get('loss', 0)),
                                    'reward': float(latest.get('reward', 0)),
                                    'lr': float(latest.get('learning_rate', 0)),
                                    'timestamp': int(time.time() * 1000),
                                    'status': f'Active - Run: {latest_run.name}'
                                }
                        except Exception as e:
                            print(f"Error fetching wandb data: {str(e)}")
                            with self.status_lock:
                                self.latest_status = {
                                    'epoch': 0,
                                    'step': 0,
                                    'loss': 0.0,
                                    'reward': 0.0,
                                    'lr': 0.0,
                                    'timestamp': int(time.time() * 1000),
                                    'status': f'Error: {str(e)}'
                                }
                        
                        time.sleep(1)  # Check for updates every second
                        
                except wandb.errors.NotFoundError:
                    print(f"Project not found: {project_path}")
                    with self.status_lock:
                        self.latest_status = {
                            'epoch': 0,
                            'step': 0,
                            'loss': 0.0,
                            'reward': 0.0,
                            'lr': 0.0,
                            'timestamp': int(time.time() * 1000),
                            'status': f'Project {project_path} not found. Please create it first.'
                        }
                    
            except Exception as e:
                print(f"Error in wandb listener: {str(e)}")
                with self.status_lock:
                    self.latest_status = {
                        'epoch': 0,
                        'step': 0,
                        'loss': 0.0,
                        'reward': 0.0,
                        'lr': 0.0,
                        'timestamp': int(time.time() * 1000),
                        'status': f'Error: {str(e)}'
                    }

        Thread(target=update_loop, daemon=True).start()

    def get_status(self):
        with self.status_lock:
            if self.latest_status is None:
                return "No training information available yet."
            
            status = self.latest_status
            return f"""🤖 Training Status:
📊 Epoch: {status['epoch']}
📈 Step: {status['step']}
📉 Loss: {status['loss']:.4f}
🎯 Reward: {status['reward']:.4f}
📚 Learning Rate: {status['lr']:.6f}
⏰ Last Update: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(status['timestamp']/1000))}
ℹ️ Status: {status['status']}""" 