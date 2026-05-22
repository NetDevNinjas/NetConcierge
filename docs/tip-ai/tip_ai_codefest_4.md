---
title: TIP.ai CodeFest 4.0
description: TIP.ai SDK references and CodeFest 4.0 guidelines
---

# TIP.ai CodeFest 4.0 
 
Here is the repo and documentation for the tip-sdk: 

- git: https://git.marriott.com/emerging-tech/tip-sdk
- confluence: https://marriottcloud.atlassian.net/wiki/spaces/GAI/pages/2923561662/TIP+SDK+Documentation
 
There is support for Python, JS/TS, Java, and Rust (if you are feeling frisky).

The python library is available on Artifactory:

```bash
# Set ARTIFACTORY_USERNAME and ARTIFACTORY_TOKEN as env variables
# Set TIP_API_KEY as env variable also add TIP_BASE_URL if using a different litellm endpoint

pip install tip-sdk \
    --index-url "https://${ARTIFACTORY_USERNAME}:${ARTIFACTORY_TOKEN}@artifactory.marriott.com/artifactory/api/pypi/emer
  gingtech-pypi-local/simple/"
```

There is also a python learning environment (jupyter lab ui preloaded with 65+ learning paths in easy to follow notebooks) available as a container on Artifactory:

```bash
docker pull artifactory.marriott.com/emergingtech/emergingtech-tipai-playground:latest

# Set TIP_API_KEY as env variable also add TIP_BASE_URL if using a different litellm endpoint

docker run --rm -it -p 8888:8888 \
  -e TIP_API_KEY="$TIP_API_KEY" \
  -e ARTIFACTORY_USERNAME="$ARTIFACTORY_USERNAME" \
  -e ARTIFACTORY_TOKEN="$ARTIFACTORY_TOKEN" \
  artifactory.marriott.com/emergingtech/emergingtech-tipai-playground:latest
```

Or using podman:

```bash
podman pull artifactory.marriott.com/emergingtech/emergingtech-tipai-playground:latest

# Set TIP_API_KEY as env variable also add TIP_BASE_URL if using a different litellm endpoint

podman run --rm -it -p 8888:8888 \
  -e TIP_API_KEY="$TIP_API_KEY" \
  -e ARTIFACTORY_USERNAME="$ARTIFACTORY_USERNAME" \
  -e ARTIFACTORY_TOKEN="$ARTIFACTORY_TOKEN" \
  artifactory.marriott.com/emergingtech/emergingtech-tipai-playground:latest
```
