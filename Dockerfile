FROM python:3.9.6-alpine3.14

ARG PIP_ENV=production

WORKDIR /code

# Prevents Python from writing *.pyc files to disc
ENV PYTHONDONTWRITEBYTECODE 1

# Prevents Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED 1

# Install app requirements
COPY ./requirements /code/requirements/

RUN set -eux && \
    pip install -U pip && \
    pip install -r /code/requirements/${PIP_ENV}.txt

# Copy the application code
COPY . /code/

# Run the application
CMD [ "python", "main.py" ]
