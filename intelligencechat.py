import os
import sqlite3
import base64
from flask import Flask, render_template, request, jsonify, session, redirect, url_for

app = Flask(__name__)
app.secret_key = "intelligence_chat_ultra_secure_key_2026_db"

# Inizializzazione Client Google GenAI
from google import genai
from google.genai import types

if not os.environ.get("GEMINI_API_KEY"):
    raise RuntimeError("Manca la variabile d'ambiente GEMINI_API_KEY nel terminale!")
client = genai.Client()

def inizializza_db():
    conn = sqlite3.connect("chat_web_system.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS utenti (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessioni (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            utente_id INTEGER,
            titolo TEXT NOT NULL,
            FOREIGN KEY (utente_id) REFERENCES utenti(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messaggi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sessione_id INTEGER,
            ruolo TEXT NOT NULL,
            testo TEXT NOT NULL,
            tipo TEXT DEFAULT 'testo', 
            FOREIGN KEY (sessione_id) REFERENCES sessioni(id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

# --- ROTTE ---

@app.route('/')
def index():
    if 'utente_id' not in session:
        return redirect(url_for('login_page'))
    return render_template('chat.html', username=session['username'])

@app.route('/login')
def login_page():
    if 'utente_id' in session:
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/signin')
def signin_page():
    if 'utente_id' in session:
        return redirect(url_for('index'))
    return render_template('signin.html')

# --- API ---

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json or {}
    u = data.get('username', '').strip()
    p = data.get('password', '').strip()
    
    if not u or not p:
        return jsonify({"success": False, "message": "Username e password non possono essere vuoti."})
    
    conn = None
    try:
        conn = sqlite3.connect("chat_web_system.db")
        c = conn.cursor()
        c.execute("INSERT INTO utenti (username, password) VALUES (?, ?)", (u, p))
        conn.commit()
        return jsonify({"success": True, "message": "Registrazione completata con successo! Ora puoi accedere."})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "message": "Questo username è già stato preso. Scegline un altro."})
    except Exception as e:
        return jsonify({"success": False, "message": f"Errore del database: {str(e)}"})
    finally:
        if conn:
            conn.close()

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    u = data.get('username', '').strip()
    p = data.get('password', '').strip()
    
    conn = sqlite3.connect("chat_web_system.db")
    c = conn.cursor()
    c.execute("SELECT id, username FROM utenti WHERE username=? AND password=?", (u, p))
    user = c.fetchone()
    conn.close()
    
    if user:
        session.clear()
        session['utente_id'] = user[0]
        session['username'] = user[1]
        session.permanent = True
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "Username o password errati."})

@app.route('/api/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

@app.route('/api/sessioni', methods=['GET', 'POST'])
def gestire_sessioni():
    if 'utente_id' not in session: return jsonify([]), 401
    conn = sqlite3.connect("chat_web_system.db")
    c = conn.cursor()
    if request.method == 'POST':
        c.execute("INSERT INTO sessioni (utente_id, titolo) VALUES (?, 'Nuova Conversazione')", (session['utente_id'],))
        conn.commit()
        sid = c.lastrowid
        conn.close()
        return jsonify({"id": sid, "titolo": "Nuova Conversazione"})
    c.execute("SELECT id, titolo FROM sessioni WHERE utente_id=? ORDER BY id DESC", (session['utente_id'],))
    rows = c.fetchall()
    conn.close()
    return jsonify([{"id": r[0], "titolo": r[1]} for r in rows])

@app.route('/api/messaggi/<int:sid>', methods=['GET'])
def prendi_messaggi(sid):
    conn = sqlite3.connect("chat_web_system.db")
    c = conn.cursor()
    c.execute("SELECT ruolo, testo, tipo FROM messaggi WHERE sessione_id=? ORDER BY id ASC", (sid,))
    rows = c.fetchall()
    conn.close()
    return jsonify([{"ruolo": r[0], "testo": r[1], "tipo": r[2]} for r in rows])

@app.route('/api/chat', methods=['POST'])
def invia_chat():
    data = request.json or {}
    sid = data.get('sessione_id')
    testo = data.get('testo')
    modello = data.get('modello')
    tipo_richiesta = data.get('tipo', 'testo')
    
    conn = sqlite3.connect("chat_web_system.db")
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM messaggi WHERE sessione_id=?", (sid,))
    if c.fetchone()[0] == 0:
        titolo = testo[:25] + "..." if len(testo) > 25 else testo
        c.execute("UPDATE sessioni SET titolo=? WHERE id=?", (titolo, sid))
        
    c.execute("INSERT INTO messaggi (sessione_id, ruolo, testo, tipo) VALUES (?, 'user', ?, 'testo')", (sid, testo))
    conn.commit()

    try:
        if tipo_richiesta == 'immagine':
            try:
                # Cambiato: rimossa la configurazione incompatibile con gemini-2.5-flash
                response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=f"Genera un'immagine dettagliata basata su questa descrizione: {testo}",
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"]
                    )
                )
                image_bytes = None
                for part in response.parts:
                    if part.inline_data:
                        image_bytes = part.inline_data.data
                        break
                
                if image_bytes:
                    image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                    risposta_ai = f"data:image/jpeg;base64,{image_base64}"
                    tipo_risposta = "immagine"
                else:
                    risposta_ai = "⚠️ Il modello non ha restituito dati grafici. Riprova con un prompt più dettagliato."
                    tipo_risposta = "testo"
            except Exception as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    risposta_ai = "🛑 Limite di generazione immagini esaurito per questo minuto. Attendi 30 secondi e riprova, o usa la chat."
                else:
                    risposta_ai = f"Non è stato possibile generare l'immagine: {str(e)}"
                tipo_risposta = "testo"
        else:
            model_id = 'gemini-2.5-flash'
            config = None
            if "Ricerca Web" in modello:
                config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())], temperature=0.3)
            elif "Pro" in modello:
                model_id = 'gemini-2.5-pro'
                config = types.GenerateContentConfig(temperature=0.4)
            else:
                config = types.GenerateContentConfig(temperature=0.7)
                
            if tipo_richiesta == 'codice':
                testo = f"Scrivi un codice basato su questa richiesta. Restituisci SOLO il codice: {testo}"

            c.execute("SELECT ruolo, testo FROM messaggi WHERE sessione_id=? AND tipo='testo' ORDER BY id ASC", (sid,))
            contents = [types.Content(role=r, parts=[types.Part.from_text(text=t)]) for r, t in c.fetchall()]
            response = client.models.generate_content(model=model_id, contents=contents, config=config)
            risposta_ai = response.text
            tipo_risposta = "codice" if tipo_richiesta == 'codice' else "testo"
            
    except Exception as general_e:
        risposta_ai = f"Errore generale del sistema: {str(general_e)}"
        tipo_risposta = "testo"

    c.execute("INSERT INTO messaggi (sessione_id, ruolo, testo, tipo) VALUES (?, 'model', ?, ?)", (sid, risposta_ai, tipo_risposta))
    conn.commit()
    conn.close()
    return jsonify({"ruolo": "model", "testo": risposta_ai, "tipo": tipo_risposta})

if __name__ == "__main__":
    inizializza_db()
    app.run(debug=True, port=5000)
