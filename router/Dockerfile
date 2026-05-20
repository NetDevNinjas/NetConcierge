FROM alpine:3.21

RUN apk add --no-cache \
    iptables \
    iproute2 \
    curl \
    python3 \
    py3-pip \
    bash

RUN pip3 install --no-cache-dir flask --break-system-packages

COPY entrypoint.sh /entrypoint.sh
COPY api.py /api.py
RUN chmod +x /entrypoint.sh

EXPOSE 5000
ENTRYPOINT ["/entrypoint.sh"]
