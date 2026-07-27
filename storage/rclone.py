import subprocess


class Rclone:

    @staticmethod
    def run(cmd):

        try:
            result = subprocess.run(
                cmd,
                check=True,
                text=True,
                capture_output=True,
            )

            return result.stdout

        except subprocess.CalledProcessError as e:

            raise RuntimeError(
                f"""
RCLONE FAILED

COMMAND:
{' '.join(cmd)}

EXIT CODE:
{e.returncode}

STDOUT:
{e.stdout}

STDERR:
{e.stderr}
"""
            )

    @staticmethod
    def copy(source: str, destination: str):
        return Rclone.run([
            "rclone",
            "copy",
            source,
            destination,
            "--create-empty-src-dirs",
        ])

    @staticmethod
    def sync(source: str, destination: str):
        return Rclone.run([
            "rclone",
            "sync",
            source,
            destination,
            "--create-empty-src-dirs",
        ])

    @staticmethod
    def check(source: str, destination: str):
        return Rclone.run([
            "rclone",
            "check",
            source,
            destination,
        ])

    @staticmethod
    def ls(remote: str):
        return Rclone.run([
            "rclone",
            "ls",
            remote,
        ])

    @staticmethod
    def delete(remote: str):
        return Rclone.run([
            "rclone",
            "deletefile",
            remote,
        ])