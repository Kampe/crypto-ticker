FROM balenalib/raspberry-pi-python:3.9

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN mkdir /code

WORKDIR /code

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential git make python3-dev python3-pillow && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/*

RUN cd /opt && \
    git clone --depth 1 https://github.com/hzeller/rpi-rgb-led-matrix.git && \
    cd rpi-rgb-led-matrix && \
    make build-python PYTHON=$(which python3) && \
    make install-python PYTHON=$(which python3) && \
    rm -rf /opt/rpi-rgb-led-matrix/.git

COPY ./requirements.txt /code/requirements.txt

RUN pip3 install --no-cache-dir -r requirements.txt

COPY . /code/

ENTRYPOINT ["python3", "ticker.py"]
