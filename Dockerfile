FROM python:3.11-slim
CMD ["python", "-c", "import os; print('Hello'); print(dict(os.environ))"]
