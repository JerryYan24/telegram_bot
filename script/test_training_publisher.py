#!/usr/bin/env python3
import lcm
import time
import random
from train_msgs import TrainStatus

def main():
    # Initialize LCM
    lc = lcm.LCM()
    
    # Training parameters
    epoch = 0
    step = 0
    base_loss = 1.0
    base_reward = 0.0
    base_lr = 0.001
    
    print("🚀 Starting training status publisher...")
    print("Press Ctrl+C to stop")
    
    try:
        while True:
            # Create and populate the message
            msg = TrainStatus()
            msg.timestamp = int(time.time() * 1000)  # Current time in milliseconds
            msg.epoch = epoch
            msg.step = step
            msg.loss = base_loss * (0.95 ** epoch) * (1 + 0.1 * random.random())
            msg.reward = base_reward + (0.1 * epoch) * (1 + 0.2 * random.random())
            msg.lr = base_lr * (0.95 ** epoch)
            
            # Publish the message
            lc.publish("TRAIN_STATUS", msg.encode())
            
            # Print status
            print(f"\r📊 Epoch: {epoch}, Step: {step}, Loss: {msg.loss:.4f}, Reward: {msg.reward:.4f}", end="")
            
            # Update counters
            step += 1
            if step >= 100:  # 100 steps per epoch
                step = 0
                epoch += 1
            
            # Sleep for a short time
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        print("\n\n👋 Stopping training status publisher...")

if __name__ == "__main__":
    main() 