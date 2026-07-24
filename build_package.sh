#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:-}"
BUILD_FLAG="${2:-}"

if [[ -z "$IMAGE" ]]; then
    echo "Usage: $0 <container_name> [build]" >&2
    exit 1
fi


CURRENT_UID=$(id -u)
CURRENT_GID=$(id -g)
# If not in container, run the script inside container
if [[ -z "${BUILD_FLAG}" ]]; then
    docker run --rm -it \
        -e "HOST_UID=$CURRENT_UID" \
        -e "HOST_GID=$CURRENT_GID" \
        -v "$(pwd)":/app \
        -w /app \
        "$IMAGE" \
        bash -x "$(basename "$0")" -- build
    exit 0
fi

# Inside container: ensure build flag is set correctly
if [[ "$BUILD_FLAG" != "build" ]]; then
    echo "Usage: $0 <container_name> build" >&2
    exit 1
fi

# Build logic
CURRENT_UID="${HOST_UID:-$(id -u)}"
CURRENT_GID="${HOST_GID:-$(id -g)}"

# Check for dnf or yum
if ! command -v dnf >/dev/null 2>&1 && ! command -v yum >/dev/null 2>&1; then
    echo "Neither dnf nor yum found" >&2
    exit 1
fi

SPEC="ispmanager-plugin-nginx_mod_rewrite_plugin.spec"
if [ ! -f "$SPEC" ]; then
    echo "Spec file $SPEC not found" >&2
    exit 1
fi

# Install BuildRequires and rpmbuild
BR=$(grep -E "^[[:space:]]*BuildRequires:" "$SPEC" | \
    sed -e "s/^[[:space:]]*BuildRequires:[[:space:]]*//" | tr "," "\n" | tr -d "[:space:]" | sort -u)
PKGS="$BR rpm-build"
if command -v dnf > /dev/null 2>&1; then
    dnf -y install $PKGS
else
    yum -y install $PKGS
fi

# Copy current directory to /root/build
mkdir -p /root/build
cp -r /app/* /root/build/

# Parse Name and Version
NAME=$(grep "^Name:" "$SPEC" | awk '{print $2}')
VERSION=$(grep "^Version:" "$SPEC" | awk '{print $2}')
if [ -z "$NAME" ] || [ -z "$VERSION" ]; then
    echo "Failed to parse Name or Version" >&2
    exit 1
fi

BUILD_DIR="/root/${NAME}-${VERSION}"
mkdir -p "$BUILD_DIR"
cp -r /root/build/. "$BUILD_DIR"/

mkdir -p /root/SOURCES/ /root/RPMS /root/SRPMS

tar -czf /root/SOURCES/"${NAME}-${VERSION}.tar.gz" -C /root "${NAME}-${VERSION}"

# Build RPMs
rpmbuild --define "_sourcedir /root/SOURCES" \
         --define "_specdir /root" \
         --define "_builddir /root" \
         --define "_rpmdir /root/RPMS" \
         --define "_srcrpmdir /root/SRPMS" \
         --undefine "_debugsource_packages" \
         -ba "$SPEC"

# Collect RPMs
if [ -d /app/_packages ]; then
    rm -rf /app/_packages/*
fi
mkdir -p /app/_packages

find /root/RPMS /root/SRPMS -type f -name "*.rpm" -exec cp {} /app/_packages/ \;
chown -R "${CURRENT_UID}:${CURRENT_GID}" /app/_packages 2>/dev/null || true

echo "Build complete. RPMs in /app/_packages (owned by: ${CURRENT_UID}:${CURRENT_GID})"
