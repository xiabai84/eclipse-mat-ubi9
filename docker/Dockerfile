# =============================================================================
# Multi-stage build
#
# Stage 1  rpm-builder   (ubi9-minimal)
#   Downloads the Java 17 JRE RPMs and extracts them into a staging root using
#   unpackRPM.sh.  Also downloads and unpacks Eclipse MAT.
#   ubi9-minimal is used here because it ships microdnf, yumdownloader, cpio,
#   wget and unzip — none of which are needed at runtime.
#
# Stage 2  pip-builder   (ubi9-minimal)
#   Builds the Python venv inside the same UBI9-minimal base that runs in
#   production.  This guarantees that compiled C extensions (lxml, etc.) are
#   linked against the correct CPython ABI.
#
# Stage 3  runtime       (ubi9-minimal)
#   The final image.  Only runtime artefacts are copied in — no build tools,
#   no package manager cache, no download artefacts.
#
# Build from the project root:
#   docker build -f docker/Dockerfile -t eclipse-mat .
#   docker build --platform linux/amd64 -f docker/Dockerfile -t eclipse-mat .
# =============================================================================


# =============================================================================
# Stage 1 — rpm-builder
# =============================================================================
FROM registry.access.redhat.com/ubi9/ubi-minimal:latest AS rpm-builder

# Install build-time tools only — they stay in this stage and are never copied
# into the final image.
#   yum-utils   → provides yumdownloader
#   cpio        → required by unpackRPM.sh (rpm2cpio | cpio)
#   wget        → downloads the Eclipse MAT zip archive
#   unzip       → extracts the MAT zip archive
RUN microdnf install -y --nodocs \
        yum-utils \
        cpio \
        wget \
        unzip \
    && microdnf clean all \
    && rm -rf /var/cache/dnf

RUN mkdir -p /build/rpm /build/root

# ── Download Java 17 JRE RPMs ─────────────────────────────────────────────────
# We deliberately do NOT pass --resolve so that we only download Java-specific
# packages.  Base libraries (glibc, libstdc++, zlib, libgcc) are already
# present in ubi9-micro and must not be overwritten with potentially different
# builds from the ubi9-minimal repo — doing so risks subtle ABI mismatches.
#
# Packages chosen:
#   java-17-openjdk-headless  JVM runtime (no compiler, ~80 MB)
#   tzdata-java               Java timezone database
#   lksctp-tools              libsctp.so — required by java.net SCTP support
#   nspr                      Netscape Portable Runtime (libnspr4.so)
#   nss-util                  NSS utility library (libnssutil3.so)
#   nss-softokn               NSS crypto + freebl (libsoftokn3.so, libfreebl3.so)
#   nss-softokn-freebl         libfreebl3.so — FIPS-capable freebl crypto module
#
# NSS is needed for Java TLS (JSSE uses the system NSS stack on Linux).
# Without it every outbound HTTPS call from the JVM will fail.
RUN yumdownloader \
        --destdir /build/rpm \
        java-17-openjdk-headless \
        tzdata-java \
        lksctp-tools \
        nspr \
        nss-util \
        nss-softokn \
        nss-softokn-freebl

# ── Unpack RPMs into staging root ─────────────────────────────────────────────
# rpm2cpio extracts file content only — %post scriptlets (including
# update-alternatives) are NOT executed.  This means /usr/bin/java and
# /etc/alternatives/java symlinks will not exist.  We work around this by
# setting JAVA_HOME explicitly and creating a stable symlink below.
COPY docker/scripts/unpackRPM.sh /tmp/unpackRPM.sh
RUN chmod +x /tmp/unpackRPM.sh && /tmp/unpackRPM.sh

# Create a stable /usr/lib/jvm/jre-17-openjdk symlink pointing to the
# versioned directory (e.g. java-17-openjdk-17.0.14.0.7-3.el9.x86_64).
# This makes JAVA_HOME version-independent across patch releases.
RUN versioned=$(ls -d /build/root/usr/lib/jvm/java-17-openjdk-17* 2>/dev/null | head -1) \
    && if [ -n "$versioned" ]; then \
           ln -sfT "$(basename "$versioned")" \
                   /build/root/usr/lib/jvm/jre-17-openjdk; \
       else \
           echo "ERROR: java-17-openjdk JVM directory not found after RPM extraction" >&2; \
           exit 1; \
       fi

# ── Download and extract Eclipse MAT ─────────────────────────────────────────
RUN wget --progress=bar:force \
        -O /tmp/mat.zip \
        "https://www.eclipse.org/downloads/download.php?file=/mat/1.16.1/rcp/MemoryAnalyzer-1.16.1.20250109-linux.gtk.x86_64.zip&r=1" \
    && unzip /tmp/mat.zip -d /opt/ \
    && rm /tmp/mat.zip \
    && mv /opt/mat /opt/eclipse-mat \
    && chmod +x /opt/eclipse-mat/ParseHeapDump.sh

# =============================================================================
# Stage 2 — pip-builder
# =============================================================================
FROM registry.access.redhat.com/ubi9/ubi-minimal:latest AS pip-builder

# Install Python + build tools needed to compile C extensions (lxml).
# These packages stay in this stage — only /opt/venv is carried forward.
RUN microdnf install -y --nodocs \
        python3 \
        python3-pip \
        python3-devel \
        gcc \
        libxml2-devel \
        libxslt-devel \
    && microdnf clean all \
    && rm -rf /var/cache/dnf

# Create a virtual environment and install production dependencies.
# --no-cache-dir keeps the layer small; --upgrade-pip avoids resolver warnings.
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt

# =============================================================================
# Stage 3 — runtime
# =============================================================================
FROM registry.access.redhat.com/ubi9/ubi-minimal:latest AS runtime

LABEL maintainer="eclipse-mat-service" \
      description="Eclipse MAT heap analysis + FastAPI REST service"

# ── Runtime packages only ────────────────────────────────────────────────────
# python3        → CPython interpreter (FastAPI service)
# bash           → entrypoint.sh and analysis shell scripts
# unzip          → report_script.sh extracts ZIP contents at runtime
# fontconfig     → required by Java AWT/BIRT chart rendering in MAT reports
# dejavu-sans-fonts → basic font set for MAT pie charts and report graphics
RUN microdnf install -y --nodocs \
        python3 \
        bash \
        unzip \
        fontconfig \
        dejavu-sans-fonts \
    && microdnf clean all \
    && rm -rf /var/cache/dnf

# ── Java 17 JRE from rpm-builder ────────────────────────────────────────────
# Copy only the JVM tree and its runtime dependencies — not the full /build/root
# which would overwrite system files (glibc, etc.) in this image.
COPY --from=rpm-builder /build/root/usr/lib/jvm /usr/lib/jvm
COPY --from=rpm-builder /build/root/etc/java    /etc/java
COPY --from=rpm-builder /build/root/etc/.java   /etc/.java
COPY --from=rpm-builder /build/root/usr/share/javazi     /usr/share/javazi
COPY --from=rpm-builder /build/root/usr/share/javazi-1.8 /usr/share/javazi-1.8

# NSS / NSPR shared libraries — required for Java TLS (JSSE).
# Copy only the specific libs we need; wildcard keeps it forward-compatible.
COPY --from=rpm-builder /build/root/usr/lib64/libnspr4.so     /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libplds4.so     /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libplc4.so      /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libnssutil3.so  /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libsoftokn3.so  /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libfreebl3.so      /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libfreeblpriv3.so  /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libsoftokn3.chk   /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libfreebl3.chk    /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libfreeblpriv3.chk /usr/lib64/
COPY --from=rpm-builder /build/root/usr/lib64/libsctp.so.1    /usr/lib64/

# ── Eclipse MAT from rpm-builder ────────────────────────────────────────────
COPY --from=rpm-builder /opt/eclipse-mat /opt/eclipse-mat

# ── Python venv from pip-builder ────────────────────────────────────────────
COPY --from=pip-builder /opt/venv /opt/venv

# ── Application code and scripts ────────────────────────────────────────────
COPY backend/ /opt/mat-service/
COPY docker/scripts/entrypoint.sh     /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh \
    && chmod -R a+rX /opt/mat-service /opt/venv

# ── Environment ─────────────────────────────────────────────────────────────
ENV JAVA_HOME=/usr/lib/jvm/jre-17-openjdk \
    PATH="/opt/venv/bin:/usr/lib/jvm/jre-17-openjdk/bin:/usr/local/bin:${PATH}" \
    MAT_TIMEOUT=600 \
    UVICORN_WORKERS=4 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ── Company CA certificates (uncomment to add) ────────────────────────────
# Place PEM-formatted .crt files in certs/ at the project root, then:
# COPY certs/*.crt /etc/pki/ca-trust/source/anchors/
# RUN update-ca-trust

# ── Non-root user ───────────────────────────────────────────────────────────
RUN echo "mat:x:1001:1001::/home/mat:/sbin/nologin" >> /etc/passwd \
    && echo "mat:x:1001:" >> /etc/group

RUN mkdir -p /home/mat /heapdumps /reports \
    && chown mat:mat /home/mat /heapdumps /reports

USER mat
WORKDIR /home/mat

EXPOSE 8080

VOLUME ["/heapdumps", "/reports"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["service"]
