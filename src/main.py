import multiprocessing
import os
import subprocess

from src import cloudfront, logger, models, squire

LOGGER = logger.build_logger()


def distribution_handler(public_url: str) -> None:
    """Updates the cloudfront distribution in a dedicated process.

    Args:
        public_url: Public URL from ngrok, that has to be updated.
    """
    cloud_front = cloudfront.CloudFront(env_dump=models.env.model_dump())
    if models.env.distribution_id:
        cloud_front.update_distribution(origin_name=public_url.lstrip("https://"))
    else:
        # fixme: Untested code
        # todo: Need to nest into the config file to update the public_url
        cloud_front.create_distribution()


def writer(frame: str) -> None:
    """Extracts the message part from each line into a custom message format.

    Args:
        frame: Each line of log message from ngrok.
    """
    try:
        level = frame.split("lvl=")[1].split()[0]
    except IndexError:
        print(frame)
        return
    if level == "info":
        log = LOGGER.info
    elif level == "warn":
        log = LOGGER.warning
    elif level in ("err", "error"):
        log = LOGGER.error
    else:
        return
    msg = frame.split("msg=")[-1].replace('"', "").replace("'", "")
    if "url=" in msg and not models.concurrency.cloudfront_process:
        public_url = msg.split("url=")[-1].strip()
        LOGGER.info(
            "Tunneling http://%s:%s through the public URL: %s",
            models.env.host,
            models.env.port,
            public_url,
        )
        process = multiprocessing.Process(
            target=distribution_handler, args=(public_url,)
        )
        process.name = "distribution-handler"
        process.start()
        models.concurrency.cloudfront_process = process
    log(msg)


def tunnel(**kwargs) -> None:
    """Initiates a ngrok tunnel using the CLI and updates cloudfront distribution."""
    if env_file := kwargs.get("env_file"):
        models.env = squire.env_loader(env_file)
    elif os.path.isfile(".env"):
        models.env = squire.env_loader(".env")
    else:
        models.env = models.EnvConfig(**kwargs)
    squire.validate_env()

    # https://ngrok.com/docs/agent/config/
    command = f'ngrok http {models.env.port} --log "stdout"'
    if models.env.ngrok_config:
        command += f" --config {models.env.ngrok_config}"

    process = subprocess.Popen(
        command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    while True:
        try:
            output = process.stdout.readline().decode().strip()
            if process.poll() and not output:
                break
            writer(output)
        except KeyboardInterrupt:
            LOGGER.warning("Tunneling interrupted")
            if models.concurrency.cloudfront_process:
                models.concurrency.cloudfront_process.join(timeout=3)
                models.concurrency.cloudfront_process.terminate()
            break
    process.kill()
    LOGGER.warning("Connection closed")
