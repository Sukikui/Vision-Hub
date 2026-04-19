FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends dnsmasq-base \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["dnsmasq"]
CMD ["--no-daemon", "--conf-file=/etc/dnsmasq.d/vision-hub.conf"]
