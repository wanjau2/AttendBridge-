from flask import Flask, request, Response
import logging

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

@app.route("/iclock/cdata", methods=["POST"])
def cdata():
    raw = request.data.decode(errors="ignore")

    logging.info("==== RAW ATTLOG START ====")
    logging.info(raw)
    logging.info("==== RAW ATTLOG END ====")

    return Response("OK")

@app.route("/iclock/getrequest", methods=["GET"])
def getrequest():
    # Device polls this — just respond empty
    return Response("OK")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)