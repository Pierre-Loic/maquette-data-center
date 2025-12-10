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
    {
        "name": "Refroidissement actif eau 💧",
        "temp_url": "http://192.168.137.12:8000/metrics/temperature",
        "ollama_url": "http://192.168.137.12:8000/ollama/generate",
    },
]

"""
RASPBERRIES = [
    {
        "name": "Refroidissement passif ♨️",
        "temp_url": "http://127.0.0.1:8000/metrics/temperature",
        "ollama_url": "http://127.0.0.1:8000/ollama/generate",
    },
    {
        "name": "Refroidissement actif air 💨",
        "temp_url": "http://127.0.0.1:8001/metrics/temperature",
        "ollama_url": "http://127.0.0.1:8001/ollama/generate",
    },
    {
         "name": "Refroidissement actif eau 💧",
         "temp_url": "http://127.0.0.1:8002/metrics/temperature",
         "ollama_url": "http://127.0.0.1:8002/ollama/generate",
     },
]
"""


current_game = {
    "started_at": None,
    "prompt": None,
    "predictions": None,
    "ollama_responses": None,
    "player_name": None,
    "window_start": None,  
    "window_end": None,   
}

temperature_samples = [] 

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

    # On stocke l’échantillon
    temperature_samples.append((now, temps))

    # Nettoyage : on garde les 10 dernières minutes max
    cutoff = now - 600
    while temperature_samples and temperature_samples[0][0] < cutoff:
        temperature_samples.pop(0)

    return jsonify({
        "timestamp": time.strftime("%H:%M:%S"),
        "temperatures": temps
    })

@app.route("/api/start_game", methods=["POST"])
def start_game():
    global current_game

    try:
        data = request.get_json() or {}
        prompt = data.get("prompt", "")
        predictions = data.get("predictions", {}) or {}

        # début de la fenêtre : juste avant l'envoi des prompts
        window_start = time.time()

        ollama_responses = {}

        # --- APPELS OLLAMA EN PARALLÈLE ---
        with ThreadPoolExecutor(max_workers=len(RASPBERRIES)) as executor:
            futures = [
                executor.submit(call_ollama, rpi, prompt)
                for rpi in RASPBERRIES
            ]

            for future in as_completed(futures):
                name, response = future.result()
                ollama_responses[name] = response
        # --- FIN PARALLÈLE ---

        last_response_at = time.time()
        window_end = last_response_at + 10.0   # 10 s après la fin de l’inférence

        current_game["prompt"] = prompt
        current_game["predictions"] = predictions
        current_game["ollama_responses"] = ollama_responses
        current_game["window_start"] = window_start
        current_game["window_end"] = window_end

        duration = int(window_end - window_start)

        return jsonify({
            "status": "started",
            "measurement_window_seconds": duration,
            "ollama_responses": ollama_responses,
        })

    except Exception as e:
        # log détaillé côté serveur
        app.logger.exception("Erreur dans /api/start_game")
        # JSON propre côté front (pour que res.json() ne plante pas)
        return jsonify({
            "status": "error",
            "message": f"Exception côté serveur: {str(e)}"
        }), 500
    
    
@app.route("/api/game_status")
def game_status():
    window_start = current_game.get("window_start")
    window_end = current_game.get("window_end")

    if window_start is None or window_end is None:
        return jsonify({"status": "no_game"})

    now = time.time()

    if now < window_end:
        remaining = int(window_end - now)
        return jsonify({
            "status": "waiting",
            "remaining_seconds": remaining,
            "ollama_responses": current_game.get("ollama_responses"),
        })

    # Fenêtre terminée → on calcule les max à partir de l’historique
    # On garde uniquement les échantillons dans la fenêtre
    relevant_samples = [
        temps for (ts, temps) in temperature_samples
        if window_start <= ts <= window_end
    ]

    # Liste des noms de RPi
    rpi_names = [r["name"] for r in RASPBERRIES]

    max_temps = {}
    for name in rpi_names:
        values = []
        for temps in relevant_samples:
            t = temps.get(name)
            if t is not None:
                values.append(t)
        max_temps[name] = max(values) if values else None

    predictions = current_game["predictions"] or {}

    errors = {}
    score = 0.0

    for name in rpi_names:
        try:
            predicted = float(predictions.get(name))
        except (TypeError, ValueError):
            predicted = None

        actual = max_temps.get(name)

        if actual is None or predicted is None:
            errors[name] = None
            continue

        err = round(abs(actual - predicted)/actual*100, 2) 
        errors[name] = err
        score += max(0.0, 10.0 - err)

    return jsonify({
        "status": "finished",
        "actual_temperatures": max_temps,   # vrai max dans la fenêtre
        "errors": errors,
        "score": score,
        "ollama_responses": current_game.get("ollama_responses"),
        "window_start": window_start,
        "window_end": window_end,
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
