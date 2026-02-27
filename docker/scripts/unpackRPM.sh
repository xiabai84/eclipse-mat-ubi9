#!/usr/bin/env sh
# unpackRPM.sh — Extract every RPM in RPM_DIR into DEST_DIR using rpm2cpio.
#
# rpm2cpio / cpio extract file content only — RPM %post scriptlets are NOT
# executed.  Callers must create any symlinks that scriptlets would have made
# (e.g. /usr/lib/jvm/jre-17-openjdk → versioned JVM directory).
#
# Usage (defaults):
#   RPM_DIR=/build/rpm  DEST_DIR=/build/root  ./unpackRPM.sh
#
# The /etc/passwd and /etc/group files shipped inside RPMs are removed from
# DEST_DIR afterwards to prevent them from overwriting the runtime image's
# user database when the layer is COPYd.

RPM_DIR=${RPM_DIR:-/build/rpm}
DEST_DIR=${DEST_DIR:-/build/root}

mkdir -p "${DEST_DIR}"

for RPM in "${RPM_DIR}"/*.rpm; do
    echo "Unpacking: ${RPM}"
    rpm2cpio "${RPM}" | cpio \
        --extract \
        --directory "${DEST_DIR}" \
        --no-absolute-filenames \
        --make-directories \
        --preserve-modification-time
done

# Remove files that must not overwrite the runtime image's user/group database.
rm -rfv "${DEST_DIR}/etc/passwd" "${DEST_DIR}/etc/group"
