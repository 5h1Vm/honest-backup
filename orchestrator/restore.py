"""
Restore functionality for HonestBackup.

This module provides functions to list, fetch, verify, decrypt, decompress,
extract, and restore backups from the repository.
"""

import os
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from storage.repository import Repository
from storage.artifact import BackupArtifact
from orchestrator.config import WORKSPACE


def list_backups() -> List[str]:
    """
    List available backup IDs in the repository.

    Returns:
        List of backup IDs (strings) sorted from newest to oldest.
    """
    # Deliberately not gated on REPOSITORY_ENABLED. Every run writes its
    # archive to the local vault before syncing anywhere, whatever that flag
    # says, so gating the *listing* on it made "--restore --list" report no
    # backups while the archives sat right there and the interface listed
    # them. Restore should show whatever actually exists.
    repo = Repository()
    backups = repo.list_backups()
    # Sort by name (which is timestamp) descending
    return sorted(backups, reverse=True)


def fetch_backup_artifact(backup_id: str, download_dir: Path) -> Optional[BackupArtifact]:
    """
    Download the backup artifact (archive, hash, manifest) for the given backup ID.

    Args:
        backup_id: The ID of the backup to download.
        download_dir: Directory to download the artifact files to.

    Returns:
        BackupArtifact object if successful, None otherwise.
    """
    # Not gated on REPOSITORY_ENABLED: every run writes its archive to the
    # local vault whatever that flag says, so gating the read made restore
    # fail with "Failed to download" while the archive sat right there.
    repo = Repository()

    # Ensure download directory exists
    download_dir.mkdir(parents=True, exist_ok=True)

    # Define expected file paths in the download directory
    archive_path = download_dir / f"{backup_id}.tar.zst.age"
    hash_path = download_dir / f"{backup_id}.sha256"
    manifest_path_new = download_dir / f"{backup_id}.manifest.json"
    manifest_path_old = download_dir / "manifest.json"

    # Check if archive and hash exist in the repository
    repo_archive = repo.root / "archives" / f"{backup_id}.tar.zst.age"
    repo_hash = repo.root / "hashes" / f"{backup_id}.sha256"
    # Check for manifest: try new format first, then old format
    repo_manifest_new = repo.root / "manifests" / f"{backup_id}.manifest.json"
    repo_manifest_old = repo.root / "manifests" / "manifest.json"

    if not (repo_archive.exists() and repo_hash.exists() and (repo_manifest_new.exists() or repo_manifest_old.exists())):
        return None

    # Copy archive and hash
    try:
        shutil.copy2(repo_archive, archive_path)
        shutil.copy2(repo_hash, hash_path)
    except Exception:
        # Clean up on failure
        archive_path.unlink(missing_ok=True)
        hash_path.unlink(missing_ok=True)
        return None

    # Copy manifest: prefer new format if exists, otherwise old format
    if repo_manifest_new.exists():
        try:
            shutil.copy2(repo_manifest_new, manifest_path_new)
            manifest_path = manifest_path_new
        except Exception:
            # Clean up on failure
            archive_path.unlink(missing_ok=True)
            hash_path.unlink(missing_ok=True)
            manifest_path_new.unlink(missing_ok=True)
            return None
    else:
        try:
            shutil.copy2(repo_manifest_old, manifest_path_old)
            manifest_path = manifest_path_old
        except Exception:
            # Clean up on failure
            archive_path.unlink(missing_ok=True)
            hash_path.unlink(missing_ok=True)
            manifest_path_old.unlink(missing_ok=True)
            return None

    return BackupArtifact(
        backup_id=backup_id,
        created=None,  # We don't have the creation time here; could be read from manifest if needed
        archive=archive_path,
        sha256=hash_path,
        manifest=manifest_path,
        report=None,  # We don't download the report by default
        size=archive_path.stat().st_size,
    )


def verify_artifact(artifact: BackupArtifact) -> bool:
    """
    Verify the backup artifact by checking its SHA256 hash.

    Args:
        artifact: The BackupArtifact to verify.

    Returns:
        True if the artifact's SHA256 matches the hash file, False otherwise.
    """
    if not artifact.sha256.exists():
        return False

    # Compute SHA256 of the archive
    computed_hash = _sha256_file(artifact.archive)
    # Read the expected hash from the hash file
    expected_hash = artifact.sha256.read_text().strip()

    return computed_hash == expected_hash


def decrypt_archive(
    encrypted_archive: Path,
    private_key: str,
    output_path: Path,
) -> bool:
    """
    Decrypt an age-encrypted archive using the provided private key.

    Args:
        encrypted_archive: Path to the encrypted archive (.age file).
        private_key: The age private key (as a string) or path to a file containing the key.
        output_path: Path to write the decrypted output.

    Returns:
        True if decryption succeeded, False otherwise.
    """
    # Determine if private_key is a path or the key itself
    if os.path.exists(private_key):
        key_arg = ["-i", private_key]
    else:
        # Assume it's the key itself; write to a temporary file
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write(private_key)
            key_file = f.name
        key_arg = ["-i", key_file]
        # We'll clean up the temp file after

    try:
        cmd = ["age", "-d"] + key_arg + ["-o", str(output_path), str(encrypted_archive)]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Decryption failed: {result.stderr}")
            return False
        return True
    finally:
        # Clean up temp file if we created one
        if not os.path.exists(private_key) and 'key_file' in locals():
            os.unlink(key_file)


def decompress_zstd(zstd_file: Path, output_path: Path) -> bool:
    """
    Decompress a Zstandard compressed file.

    Args:
        zstd_file: Path to the .zst file.
        output_path: Path to write the decompressed output.

    Returns:
        True if decompression succeeded, False otherwise.
    """
    try:
        cmd = ["zstd", "-d", str(zstd_file), "-o", str(output_path)]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Decompression failed: {result.stderr}")
            return False
        return True
    except FileNotFoundError:
        print("zstd command not found. Please install zstd.")
        return False


def extract_tar(tar_file: Path, output_dir: Path) -> bool:
    """
    Extract a tar file to the specified directory.

    Args:
        tar_file: Path to the tar file.
        output_dir: Directory to extract the contents to.

    Returns:
        True if extraction succeeded, False otherwise.
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        cmd = ["tar", "-xf", str(tar_file), "-C", str(output_dir)]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Extraction failed: {result.stderr}")
            return False
        return True
    except FileNotFoundError:
        print("tar command not found. Please install tar.")
        return False


def list_backup_contents(tar_file: Path) -> List[str]:
    """
    List the contents of a tar file without extracting.

    Args:
        tar_file: Path to the tar file.

    Returns:
        List of file paths in the tar archive.
    """
    try:
        cmd = ["tar", "-tf", str(tar_file)]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        print(f"Failed to list tar contents: {e}")
        return []


def restore_backup(
    backup_id: str,
    restore_dir: Path,
    private_key: str,
    selective_files: Optional[List[str]] = None,
    temp_dir: Optional[Path] = None,
) -> bool:
    """
    Restore a backup from the repository.

    This function will:
    1. Download the backup artifact (archive, hash, manifest)
    2. Verify the archive's SHA256 hash
    3. Decrypt the archive using the provided private key
    4. Decompress the zstd-compressed tar file
    5. Extract the tar file (either fully or selectively)

    Args:
        backup_id: The ID of the backup to restore.
        restore_dir: Directory to restore the backup to.
        private_key: The age private key (as a string or path to key file).
        selective_files: Optional list of file paths to restore (relative to the backup root).
                         If None, the entire backup is restored.
        temp_dir: Optional directory to use for temporary files. If not provided, a temporary directory is created.

    Returns:
        True if the restore succeeded, False otherwise.
    """
    # Create a temporary directory if not provided
    temp_cleanup = False
    if temp_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="honestbackup_restore_"))
        temp_cleanup = True
    else:
        temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Step 1: Download the backup artifact
        print(f"Downloading backup {backup_id}...")
        artifact = fetch_backup_artifact(backup_id, temp_dir)
        if artifact is None:
            print(f"Failed to download backup {backup_id}")
            return False

        # Step 2: Verify the artifact
        print("Verifying backup integrity...")
        if not verify_artifact(artifact):
            print("Backup verification failed!")
            return False
        print("Backup verified successfully.")

        # Step 3: Decrypt the archive
        decrypted_file = temp_dir / f"{backup_id}.tar.zst"
        print("Decrypting archive...")
        if not decrypt_archive(artifact.archive, private_key, decrypted_file):
            print("Decryption failed!")
            return False
        print("Decryption successful.")

        # Step 4: Decompress the zstd file
        tar_file = temp_dir / f"{backup_id}.tar"
        print("Decompressing archive...")
        if not decompress_zstd(decrypted_file, tar_file):
            print("Decompression failed!")
            return False
        print("Decompression successful.")

        # Step 5: Extract the tar file
        if selective_files is None:
            # Full restore
            print("Extracting backup...")
            if not extract_tar(tar_file, restore_dir):
                print("Extraction failed!")
                return False
            print(f"Backup restored to {restore_dir}")
        else:
            # Selective restore: extract to a temporary location then copy requested files
            extract_dir = temp_dir / "extracted"
            print("Extracting backup to temporary location for selective restore...")
            if not extract_tar(tar_file, extract_dir):
                print("Extraction failed!")
                return False

            # Copy requested files
            restored_count = 0
            for rel_path in selective_files:
                src_path = extract_dir / rel_path
                dst_path = restore_dir / rel_path
                if not src_path.exists():
                    print(f"Warning: File {rel_path} not found in backup")
                    continue
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, dst_path)
                restored_count += 1
            print(f"Restored {restored_count} files to {restore_dir}")

        return True

    finally:
        # Clean up temporary directory if we created it
        if temp_cleanup:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _sha256_file(filepath: Path) -> str:
    """
    Compute the SHA256 hash of a file.

    Args:
        filepath: Path to the file.

    Returns:
        Hexadecimal SHA256 hash string.
    """
    import hashlib
    hash_sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest()


# Example usage (for testing or command-line interface)
if __name__ == "__main__":
    # This block is for testing only; the module is intended to be imported.
    import sys
    if len(sys.argv) < 4:
        print("Usage: python restore.py <backup_id> <restore_dir> <private_key> [file1 file2 ...]")
        sys.exit(1)

    backup_id = sys.argv[1]
    restore_dir = Path(sys.argv[2])
    private_key = sys.argv[3]
    selective_files = sys.argv[4:] if len(sys.argv) > 4 else None

    success = restore_backup(backup_id, restore_dir, private_key, selective_files)
    if success:
        print("Restore completed successfully.")
    else:
        print("Restore failed.")
        sys.exit(1)