from flask import Flask, jsonify, render_template, request
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

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
]

"""
    {
        "name": "Refroidissement actif eau 💧",
        "temp_url": "http://192.168.137.12:8000/metrics/temperature",
        "ollama_url": "http://192.168.137.12:8000/ollama/generate",
    },"""

current_game = {
    "started_at": None,
    "prompt": None,
    "predictions": None,
    "ollama_responses": None,
    "player_name": None,
    "max_temps": None,    
    "window_start": None,
    "window_end": None,
}


def call_ollama(rpi, prompt):
    name = rpi["name"]
    url = rpi["ollama_url"]
    try:
        payload = {"prompt": prompt}
        res = requests.post(url, json=payload, timeout=120)
        res.raise_for_status()
        resp_json = res.json()
        return name, resp_json.get("response", "(aucune réponse)")
    except Exception as e:
        print(f"Erreur appel Ollama pour {name} ({url}): {e}")
        return name, f"Erreur en appelant Ollama sur {name}."

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
    now = time.time()

    window_start = current_game.get("window_start")
    window_end = current_game.get("window_end")

    # Mise à jour des max UNIQUEMENT si on est dans la fenêtre
    if window_start is not None:
        # Si la fin n'est pas encore connue (les RPi n'ont pas encore toutes répondu)
        # on considère qu'on est toujours dans la fenêtre à partir de window_start.
        in_window = (now >= window_start) and (
            window_end is None or now <= window_end
        )

        if in_window:
            if current_game["max_temps"] is None:
                current_game["max_temps"] = {}
            for name, t in temps.items():
                if t is None:
                    continue
                prev = current_game["max_temps"].get(name)
                if prev is None or t > prev:
                    current_game["max_temps"][name] = t

    return jsonify({
        "timestamp": time.strftime("%H:%M:%S"),
        "temperatures": temps
    })


@app.route("/api/start_game", methods=["POST"])
def start_game():
    global current_game

    data = request.get_json() or {}
    prompt = data.get("prompt", "")
    predictions = data.get("predictions", {}) or {}

    # On reset l'état du jeu
    current_game["prompt"] = prompt
    current_game["predictions"] = predictions
    current_game["ollama_responses"] = {}
    current_game["max_temps"] = {}
    current_game["window_start"] = None
    current_game["window_end"] = None

    # 🕒 début de la fenêtre de chauffe = au moment où on envoie les prompts
    window_start = time.time()

    ollama_responses = {}

    # 🔁 Appels parallèles à Ollama
    with ThreadPoolExecutor(max_workers=len(RASPBERRIES)) as executor:
        futures = [
            executor.submit(call_ollama, rpi, prompt)
            for rpi in RASPBERRIES
        ]
        for future in as_completed(futures):
            name, resp = future.result()
            ollama_responses[name] = resp

    # ⏱️ dernière réponse reçue
    last_response_at = time.time()

    # On enregistre la fenêtre de mesure :
    #   - début : envoi des prompts
    #   - fin : 10s après la dernière réponse
    current_game["window_start"] = window_start
    current_game["window_end"] = last_response_at + 10.0

    current_game["ollama_responses"] = ollama_responses

    return jsonify({
        "status": "started",
        # temps total approximatif avant score : (window_end - window_start)
        "measurement_window_seconds": int(current_game["window_end"] - current_game["window_start"]),
        "ollama_responses": ollama_responses,
    })

@app.route("/api/game_status")
def game_status():
    window_start = current_game.get("window_start")
    window_end = current_game.get("window_end")

    if window_start is None:
        return jsonify({"status": "no_game"})

    now = time.time()

    # Si la fin de fenêtre est connue mais pas encore atteinte → on attend encore
    if window_end is None or now < window_end:
        remaining = None
        if window_end is not None:
            remaining = int(window_end - now)
        return jsonify({
            "status": "waiting",
            "remaining_seconds": remaining,
            "ollama_responses": current_game.get("ollama_responses"),
        })

    # Ici : now >= window_end → la fenêtre est finie, on utilise les max_temps
    temps = current_game.get("max_temps") or {}
    predictions = current_game["predictions"] or {}

    errors = {}
    score = 0.0

    for name, actual in temps.items():
        try:
            predicted = float(predictions.get(name))
        except (TypeError, ValueError):
            predicted = None

        if actual is None or predicted is None:
            errors[name] = None
            continue

        err = abs(actual - predicted)
        errors[name] = err
        score += max(0.0, 10.0 - err)

    return jsonify({
        "status": "finished",
        "actual_temperatures": temps,       # ce sont les max sur la fenêtre
        "errors": errors,
        "score": score,
        "ollama_responses": current_game.get("ollama_responses"),
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
