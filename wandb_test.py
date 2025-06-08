import wandb

api = wandb.Api()
runs = api.runs("jerryyan24-uc-san-diego/gsworld")

for run in runs:
    print(run.name, run.state, run.summary["loss"])
