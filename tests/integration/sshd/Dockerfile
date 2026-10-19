# A throw-away SSH server for the file transfer tests (ADR-0009).
#
# Built from the public base image alpine only. It has one user, "tester", who
# logs in by public key only; the key is given at start (AUTHORIZED_KEY). It is
# for local tests: nothing in it is a secret and it is never published.
FROM alpine:3.21

RUN apk add --no-cache openssh-server \
    && adduser -D -s /bin/sh tester \
    && sed -i 's/^tester:!/tester:*/' /etc/shadow \
    && mkdir -p /home/tester/.ssh /home/tester/work \
    && chown -R tester:tester /home/tester \
    && chmod 700 /home/tester/.ssh

COPY sshd_config /etc/ssh/sshd_config
COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
