from flask import Flask, jsonify, render_template, request
import requests
import time

app = Flask(__name__)

RASPBERRIES = [
    {
        "name": "Refroidissement passif ♨️",
        "temp_url": "http://192.168.137.10:8000/metrics/temperature",
        "ollama_url": "http://192.168.137.10:8000/ollama/generate",
    },
    {
        "name": "Refroidissement actif air 💨",
        "temp_url": "http://192.168.137.11:8000/metrics/temperature",
        "ollama_url": "http://192.168.137.11:8000/ollama/generate",
    },
    {
        "name": "Refroidissement actif eau 💧",
        "temp_url": "http://192.168.137.12:8000/metrics/temperature",
        "ollama_url": "http://192.168.137.12:8000/ollama/generate",
    },
]

# État simple du jeu (pour démo, en mémoire)
current_game = {
    "started_at": None,
    "prompt": None,
    "predictions": None,
    "ollama_responses": None,
}

def fetch_temperatures():
    temps = {}
    for rpi in RASPBERRIES:
        name = rpi["name"]
        url = rpi["temp_url"]
        try:
            res = requests.get(url, timeout=2)
            res.raise_for_status()
            data = res.json()
            temps[name] = data.get("temperature_c")
        except Exception as e:
            print(f"Erreur pour {name} ({url}): {e}")
            temps[name] = None
    return temps

@app.route("/")
def index():
    # On passe les noms des RPi au template
    return render_template("index.html", raspberries=[r["name"] for r in RASPBERRIES])


@app.route("/api/temperatures")
def api_temperatures():
    temps = fetch_temperatures()
    return jsonify({
        "timestamp": time.strftime("%H:%M:%S"),
        "temperatures": temps
    })


@app.route("/api/start_game", methods=["POST"])
def start_game():
    """
    Lance une partie :
    - reçoit le prompt + les prédictions
    - envoie le prompt à Ollama sur les 3 Raspberry Pi
    - attendra un certain temps avant d'évaluer les prédictions
    """
    global current_game

    data = request.get_json() or {}
    prompt = data.get("prompt", "")
    predictions = data.get("predictions", {}) or {}

    # Appel à Ollama sur chaque Raspberry Pi
    ollama_responses = {}
    for rpi in RASPBERRIES:
        name = rpi["name"]
        url = rpi["ollama_url"]
        try:
            payload = {
                "prompt": prompt,
                # optionnel : "model": "llama3" si tu veux forcer un modèle
            }
            res = requests.post(url, json=payload, timeout=120)
            res.raise_for_status()
            resp_json = res.json()
            ollama_responses[name] = resp_json.get("response", "(aucune réponse)")
        except Exception as e:
            print(f"Erreur appel Ollama pour {name} ({url}): {e}")
            ollama_responses[name] = f"Erreur en appelant Ollama sur {name}."

    current_game["started_at"] = time.time()
    current_game["prompt"] = prompt
    current_game["predictions"] = predictions
    current_game["ollama_responses"] = ollama_responses

    # Par exemple, on décide que le "résultat" est évalué 30 s après le début
    delay = 30

    return jsonify({
        "status": "started",
        "check_after_seconds": delay,
        "ollama_responses": ollama_responses,
    })


@app.route("/api/game_status")
def game_status():
    """
    - Si aucune partie : status = no_game
    - Si partie en cours et délai pas encore écoulé : waiting
    - Si délai écoulé : calcule le score à partir des vraies températures
    """
    if current_game["started_at"] is None:
        return jsonify({"status": "no_game"})

    elapsed = time.time() - current_game["started_at"]
    delay = 30  # doit être le même que dans /api/start_game

    if elapsed < delay:
        return jsonify({
            "status": "waiting",
            "remaining_seconds": int(delay - elapsed),
            "ollama_responses": current_game.get("ollama_responses"),
        })

    # Délai écoulé → on récupère les températures réelles
    temps = fetch_temperatures()
    predictions = current_game["predictions"] or {}

    errors = {}
    score = 0.0

    for name in temps.keys():
        try:
            predicted = float(predictions.get(name))
        except (TypeError, ValueError):
            predicted = None

        actual = temps.get(name)

        if actual is None or predicted is None:
            errors[name] = None
            continue

        err = abs(actual - predicted)
        errors[name] = err
        # Score simple : 10 points si parfait, puis -1 pt par °C d'erreur min 0
        score += max(0.0, 10.0 - err)

    return jsonify({
        "status": "finished",
        "actual_temperatures": temps,
        "errors": errors,
        "score": score,
        "ollama_responses": current_game.get("ollama_responses"),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
