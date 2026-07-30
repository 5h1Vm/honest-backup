#!/usr/bin/env bash
#
# Put the tools this drive needs onto the drive itself.
#
# An office Mac generally has none of rclone, age or zstd, and getting
# them installed means Homebrew, an administrator password, and a machine
# left changed afterwards. So instead they are downloaded into tools/
# next to these scripts. Nothing is installed, nothing needs admin, and
# unplugging the drive leaves the machine exactly as it was.
#
#   ./get-tools.sh              tools for the machine this runs on
#   ./get-tools.sh --all        tools for macOS and Linux, both chips
#
# Run it once, on any machine with a network. The drive then works
# everywhere.
#
# The private key is never part of this. It opens every backup on the
# drive, so a copy of it travelling with the drive would mean a lost or
# stolen drive is a readable copy of everything — the archives are
# encrypted specifically to prevent that. Browse or Menu asks for the key
# each time it is needed instead; nothing about it is ever written here.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLS="$HERE/tools"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
GREEN=$'\033[32m'; YELLOW=$'\033[33m'; OFF=$'\033[0m'
ok(){   echo "  ${GREEN}✓${OFF} $*"; }
warn(){ echo "  ${YELLOW}!${OFF} $*"; }
die(){  echo; echo "  ${RED}Stopped:${OFF} $*"; echo; exit 1; }

RCLONE_VERSION="v1.68.2"
AGE_VERSION="v1.2.1"
ZSTD_VERSION="v1.5.6"

ALL_PLATFORMS=false
for arg in "$@"; do
    case "$arg" in
        --all)      ALL_PLATFORMS=true ;;
        -h|--help)  sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)          die "I do not know the option $arg" ;;
    esac
done

command -v curl >/dev/null 2>&1 || die "curl is needed to download anything."
command -v tar  >/dev/null 2>&1 || die "tar is needed to unpack the downloads."

PY=""
for candidate in python3 python; do
    command -v "$candidate" >/dev/null 2>&1 && { PY="$candidate"; break; }
done
[[ -n "$PY" ]] || die "Python 3 is needed — everything on this drive runs on it.
     macOS:  xcode-select --install
     Linux:  sudo apt install python3"

# ---------------------------------------------------------------------------
# what machine is this
# ---------------------------------------------------------------------------
case "$(uname -s)" in
    Darwin) THIS_OS=darwin ;;
    Linux)  THIS_OS=linux ;;
    *)      die "This handles macOS and Linux. For Windows, install rclone
     and age by hand and put them in $TOOLS." ;;
esac
case "$(uname -m)" in
    x86_64|amd64)  THIS_ARCH=amd64 ;;
    arm64|aarch64) THIS_ARCH=arm64 ;;
    *) die "Unfamiliar processor: $(uname -m)" ;;
esac

echo
echo "${BOLD}Fetching the tools onto the drive${OFF}"
echo "  ${DIM}into $TOOLS${OFF}"
echo "  ${DIM}this machine: $THIS_OS/$THIS_ARCH${OFF}"
echo

mkdir -p "$TOOLS" || die "cannot write to $TOOLS — is the drive read-only?"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fetch() {
    # fetch <url> <file>
    curl -fsSL --retry 3 --connect-timeout 20 -o "$2" "$1"
}

# ---------------------------------------------------------------------------
# one platform's worth of tools
#
# Each lands as tools/<name>-<os>-<arch>, and a copy under the plain name
# for the machine that ran this — view.py looks for the plain name, so the
# drive works out of the box here and can be taught other platforms with
# --all.
# ---------------------------------------------------------------------------
install_for() {
    local os="$1" arch="$2" suffix="-$1-$2"
    local native=false
    [[ "$os" == "$THIS_OS" && "$arch" == "$THIS_ARCH" ]] && native=true

    echo "  ${BOLD}$os/$arch${OFF}"

    # ---- rclone ----------------------------------------------------------
    # rclone calls macOS "osx" in its release names; age calls it
    # "darwin". Same machine, two spellings.
    local rc_os="$os"
    [[ "$os" == "darwin" ]] && rc_os="osx"
    local rc_zip="$WORK/rclone-$os-$arch.zip"
    if fetch "https://downloads.rclone.org/$RCLONE_VERSION/rclone-$RCLONE_VERSION-$rc_os-$arch.zip" "$rc_zip"; then
        # Unpacked with Python rather than unzip: this server has no
        # unzip, and Python is already a hard requirement for everything
        # else on the drive, so leaning on it costs nothing.
        local out="$WORK/rc-$os-$arch"
        mkdir -p "$out"
        if "$PY" - "$rc_zip" "$out" <<'PYTHON'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as bundle:
    for member in bundle.namelist():
        if member.rsplit("/", 1)[-1] in ("rclone", "rclone.exe"):
            bundle.extract(member, sys.argv[2])
            break
    else:
        sys.exit(1)
PYTHON
        then
            local found
            found="$(find "$out" -name 'rclone*' -type f | head -1)"
            cp "$found" "$TOOLS/rclone$suffix"
            chmod +x "$TOOLS/rclone$suffix"
            $native && cp "$found" "$TOOLS/rclone" && chmod +x "$TOOLS/rclone"
            ok "rclone"
        else
            warn "rclone archive had no binary in it"
        fi
    else
        warn "could not download rclone for $os/$arch"
    fi

    # ---- age -------------------------------------------------------------
    local age_tar="$WORK/age-$os-$arch.tar.gz"
    if fetch "https://github.com/FiloSottile/age/releases/download/$AGE_VERSION/age-$AGE_VERSION-$os-$arch.tar.gz" "$age_tar"; then
        tar -xzf "$age_tar" -C "$WORK" 2>/dev/null
        if [[ -f "$WORK/age/age" ]]; then
            cp "$WORK/age/age" "$TOOLS/age$suffix"
            chmod +x "$TOOLS/age$suffix"
            $native && cp "$WORK/age/age" "$TOOLS/age" && chmod +x "$TOOLS/age"
            rm -rf "$WORK/age"
            ok "age"
        else
            warn "age archive had no binary in it"
        fi
    else
        warn "could not download age for $os/$arch"
    fi

    # ---- zstd ------------------------------------------------------------
    # zstd publishes no plain binary for macOS, and every recent macOS and
    # Linux already ships one. So this only reports, and only nags when it
    # is the machine we are standing on.
    if $native; then
        if command -v zstd >/dev/null 2>&1; then
            cp "$(command -v zstd)" "$TOOLS/zstd" 2>/dev/null \
                && chmod +x "$TOOLS/zstd" && ok "zstd (copied from this machine)" \
                || ok "zstd (already on this machine)"
        else
            warn "zstd is not on this machine — install it with:"
            case "$os" in
                darwin) echo "        brew install zstd" ;;
                linux)  echo "        sudo apt install zstd" ;;
            esac
        fi
    fi
}

if $ALL_PLATFORMS; then
    for combination in darwin/arm64 darwin/amd64 linux/amd64 linux/arm64; do
        install_for "${combination%%/*}" "${combination##*/}"
        echo
    done
else
    install_for "$THIS_OS" "$THIS_ARCH"
    echo
fi

echo
echo "${BOLD}Done${OFF}"
echo "  ${DIM}$(ls "$TOOLS" 2>/dev/null | tr '\n' ' ')${OFF}"
echo
echo "  Now double-click ${BOLD}HonestBackup.command${OFF} on a Mac,"
echo "  or ${BOLD}copy-now.sh${OFF} on Linux."
echo
