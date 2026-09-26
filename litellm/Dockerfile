FROM docker.litellm.ai/berriai/litellm:main-stable
WORKDIR /app
COPY config.yaml /app/config.yaml
COPY custom_callbacks.py /app/custom_callbacks.py
COPY ad_hoc_recognizers.json /app/ad_hoc_recognizers.json
CMD ["--config", "/app/config.yaml", "--port", "4000"]
