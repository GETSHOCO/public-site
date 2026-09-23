FROM python:3.12-alpine

WORKDIR /app
COPY app.py .
RUN addgroup -S shoco && adduser -S -G shoco shoco && mkdir /data && chown shoco:shoco /data
USER shoco

EXPOSE 8000
CMD ["python", "app.py"]
