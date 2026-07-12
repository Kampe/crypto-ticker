FROM balenalib/raspberry-pi-python:3.11-bookworm-run

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /code

RUN printf 'Acquire::ForceIPv4 "true";\n' > /etc/apt/apt.conf.d/99force-ipv4
RUN apt-get -o Acquire::ForceIPv4=true update && \
    apt-get -o Acquire::ForceIPv4=true install -y --no-install-recommends build-essential git make python3-dev && \
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
