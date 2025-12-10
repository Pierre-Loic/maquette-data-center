from flask import Flask, jsonify, request
import time
import random
import argparse

app = Flask(__name__)

STATE = {
    "name": "Raspberry mock",
    "base_temp": 45.0,
    "temp_variation": 3.0,
    "min_delay": 0.2,
    "max_delay": 0.6,
    "ollama_min_delay": 1.0,
    "ollama_max_delay": 4.0,
}


@app.route("/metrics/temperature")
def metrics_temperature():
    """
    Simule une sonde de température.
    """
    # petite latence réseau / hardware
    time.sleep(random.uniform(STATE["min_delay"], STATE["max_delay"]))

    # random walk de la température
    STATE["base_temp"] += random.uniform(-0.3, 0.5)
    temp = STATE["base_temp"] + random.uniform(
        -STATE["temp_variation"],
        STATE["temp_variation"],
    )

    return jsonify({"temperature_c": round(temp, 2)})


@app.route("/ollama/generate", methods=["POST"])
def ollama_generate():
    """
    Simule un endpoint Ollama qui génère une réponse.
    """
    data = request.get_json() or {}
    prompt = data.get("prompt", "")

    # latence d'inférence
    time.sleep(random.uniform(STATE["ollama_min_delay"], STATE["ollama_max_delay"]))

    fake_answer = (
        f"[{STATE['name']}] Faux modèle Ollama : "
        f"j'ai reçu un prompt de {len(prompt)} caractères.\n\n"
        f"Contenu (début) : {prompt[:80]!r}..."
    )

    return jsonify({"response": fake_answer})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="Raspberry mock")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--base-temp", type=float, default=45.0)
    parser.add_argument("--variation", type=float, default=3.0)
    args = parser.parse_args()

    STATE["name"] = args.name
    STATE["base_temp"] = args.base_temp
    STATE["temp_variation"] = args.variation

    app.run(host="0.0.0.0", port=args.port, debug=True)


if __name__ == "__main__":
    main()