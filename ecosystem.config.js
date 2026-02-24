module.exports = {
  apps: [{
    name: "moon-dev-challenger",
    script: "src/main.py",
    interpreter: ".venv/bin/python3",
    env: {
      PYTHONPATH: ".",
      LIVE_TRADING_ENABLED: "False",
      USE_PAPER_TRADING: "True"
    },
    restart_delay: 5000,
    max_restarts: 10,
    error_file: "logs/err.log",
    out_file: "logs/out.log"
  }]
}
