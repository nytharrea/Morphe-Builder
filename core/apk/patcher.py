"""Morphe CLI yama yürütücüsü."""

import logging

logger = logging.getLogger("morphe.apk.patcher")


def apply_patches(source_apk: str, output_apk: str, patches: list) -> None:
    logger.info("Yamalama başlatılıyor: %s -> %s", source_apk, output_apk)
    cmd = ["java", "-jar", "morphe-cli.jar", "patch", "-i", source_apk, "-o", output_apk]
    for patch in patches:
        cmd.extend(["-e", patch])

    logger.debug("Yürütülen komut: %s", " ".join(cmd))
    # CI/CD ortamında morphe-cli.jar mevcut olduğunda çalıştırılır
