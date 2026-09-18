FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-server git python3 ca-certificates \
 && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_* \
 && groupadd --system toolhub \
 && useradd --uid 1000 --create-home --home-dir /home/git --shell /bin/sh git \
 && usermod -p '*' git \
 && usermod -aG toolhub git \
 && useradd --system --no-create-home --shell /usr/sbin/nologin toolhub-keys \
 && usermod -aG toolhub toolhub-keys \
 && mkdir -p /data/git/repositories /var/spool/toolhub /data/ssh \
 && chown -R git:git /data/git /var/spool/toolhub

# sshd yêu cầu AuthorizedKeysCommand + thư mục cha thuộc root, không ai khác ghi được.
COPY --chown=root:root lib/ /opt/toolhub/lib/
COPY --chown=root:root bin/ /opt/toolhub/bin/
COPY --chown=root:root hooks/ /opt/toolhub/hooks/
COPY --chown=root:root sshd_config /etc/ssh/sshd_config
COPY --chown=root:root entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /opt/toolhub/bin/* /opt/toolhub/hooks/* /entrypoint.sh \
 && chmod 755 /opt/toolhub /opt/toolhub/bin /opt/toolhub/hooks /opt/toolhub/lib \
 && chmod 755 /opt/toolhub/bin/* /opt/toolhub/hooks/* /entrypoint.sh \
 && chmod 644 /opt/toolhub/lib/* /etc/ssh/sshd_config

EXPOSE 22
VOLUME ["/data/ssh"]
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python3 -c "import socket; socket.create_connection(('127.0.0.1', 22), 3)"
ENTRYPOINT ["/entrypoint.sh"]
