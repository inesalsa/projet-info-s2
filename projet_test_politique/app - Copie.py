from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_migrate import Migrate
from werkzeug.security import generate_password_hash
import requests
import json
import re
from newsapi import NewsApiClient
from datetime import datetime, timedelta
from models import User, Question, Reponse, Article, db, AnalysePolitique
from my_database import save_question, save_answer
from flask_caching import Cache
from collections import defaultdict
from sqlalchemy.sql import func
import logging
import urllib.parse

# ===============================
# === Initialisation de l'App ===
# ===============================
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///my_database.db'
app.config['SECRET_KEY'] = 'ton_secret'
db.init_app(app)
migrate = Migrate(app, db)

# Configuration du cache avec un backend persistant
app.config['CACHE_TYPE'] = 'FileSystemCache'
app.config['CACHE_DIR'] = 'flask_cache'
app.config['CACHE_DEFAULT_TIMEOUT'] = 86400  # 24 heures en secondes
cache = Cache(app)

NEWS_API_KEY = '81ab1434b19c4ebb8517769bfbbf6cc9'
NEWS_API_URL = 'https://newsapi.org/v2/top-headlines'
newsapi = NewsApiClient(api_key=NEWS_API_KEY)

with app.app_context():
    db.create_all()
    
# ==================================
# ============= Routes =============
# ==================================


#Extrait le nom du site à partir de l'URL
@app.template_filter('extract_source')
def extract_source(url):
    
    if not url:
        return "Source inconnue"
    try:
        parsed_url = urllib.parse.urlparse(url)
        domain = parsed_url.netloc
        # Supprimer www. et prendre la partie principale du domaine
        domain = re.sub(r'^www\.', '', domain)
        # Extraire le nom du site (sans l'extension)
        site_name = domain.split('.')[0]
        # Mettre en majuscule la première lettre
        return site_name.capitalize()
    except:
        return "Source externe"
    
#Nettoie le contenu de l'article.  
def nettoyer_contenu(content):
    
    if content:
        content = re.sub(r'\[.*?\]', '', content)
        content = re.sub(r'\n+', ' ', content)
        return content.strip()
    return ''

#Récupère les réponses actives de l'utilisateur sous forme structurée pour faciliter l'analyse politique par Ollama.
def get_reponses_utilisateur(user_id, include_history=False):
    
    # Récupérer les réponses actives données par l'utilisateur
    reponses_obj = Reponse.query.filter_by(user_id=user_id, est_active=True, etat="répondu").all()
    
    # Log pour débogage - voir combien de réponses actives on a
    reponses_count = len(reponses_obj)
    logging.debug(f"Récupération de {reponses_count} réponses actives pour l'utilisateur {user_id}")
    
    # AJOUT: Log des détails des réponses
    for rep in reponses_obj:
        logging.debug(f"Réponse ID {rep.id}: Question {rep.question_id}, Texte: {rep.texte[:50]}...")
    
    # Structurer les réponses avec les questions pour plus de contexte
    formatted_responses = []
    
    for reponse in reponses_obj:
        question = Question.query.get(reponse.question_id)
        if question:
            formatted_responses.append(f"{question.texte} : {reponse.texte}")
            logging.debug(f"Réponse formatée: {question.texte[:30]}... : {reponse.texte}")
        else:
            logging.warning(f"Question ID {reponse.question_id} non trouvée pour la réponse {reponse.id}")
            
    # CORRECTION: Vérifier aussi les réponses avec etat="passé" si pas assez de réponses "répondu"
    if len(formatted_responses) < 3:
        logging.warning(f"Seulement {len(formatted_responses)} réponses trouvées, ajout des réponses 'passé'")
        reponses_passees = Reponse.query.filter_by(user_id=user_id, est_active=True, etat="passé").all()
        for reponse in reponses_passees:
            question = Question.query.get(reponse.question_id)
            if question:
                formatted_responses.append(f"{question.texte} : Question passée")
    
    # Si aucune réponse n'a été trouvée, on inclut un message d'erreur
    if not formatted_responses:
        logging.error(f"CRITIQUE: Aucune réponse active trouvée pour l'utilisateur {user_id}")
        # CORRECTION: Vérifier TOUTES les réponses de l'utilisateur
        toutes_reponses = Reponse.query.filter_by(user_id=user_id).all()
        logging.error(f"Total de réponses dans la DB pour cet utilisateur: {len(toutes_reponses)}")
        for rep in toutes_reponses:
            logging.error(f"Réponse: ID={rep.id}, est_active={rep.est_active}, etat={rep.etat}")
        return ["Pas de réponses disponibles"]
    
    # Si demandé, inclure l'historique des réponses précédentes
    if include_history:
        previous_responses = Reponse.query.filter_by(
            user_id=user_id, 
            est_active=False,
            etat="répondu"
        ).order_by(Reponse.date_creation.desc()).limit(30).all()
        
        previous_count = len(previous_responses)
        logging.debug(f"Récupération de {previous_count} réponses historiques pour l'utilisateur {user_id}")
        
        if previous_responses:
            formatted_responses.append("\n--- HISTORIQUE DES RÉPONSES PRÉCÉDENTES ---\n")
            for reponse in previous_responses:
                question = Question.query.get(reponse.question_id)
                if question:
                    formatted_responses.append(f"ANCIEN - {question.texte} : {reponse.texte}")
    
    logging.info(f"TOTAL: {len(formatted_responses)} réponses formatées pour l'analyse")
    return formatted_responses


#Fonction pour réinitialiser le quiz de l'utilisateur si nécessaire
def reset_quiz_for_user(user_id):
    Reponse.query.filter_by(user_id=user_id).delete()
    db.session.commit()

#Obtenir la réponse d'Ollama avec gestion d'erreur
def get_ollama_response(prompt):

    try:
        url = "http://localhost:11434/api/generate"
        data = {
            "model": "llama3.2",
            "prompt": prompt,
            "stream": False,
            "temperature": 0.3,  # Réduire pour plus de cohérence
            "max_tokens": 1500,
            "top_p": 0.9
        }
        headers = {'Content-Type': 'application/json'}

        logging.info(f"Requête Ollama: {len(prompt)} caractères")
        
        response = requests.post(url, data=json.dumps(data), headers=headers, timeout=45)
        
        if response.status_code != 200:
            logging.error(f"Ollama HTTP {response.status_code}: {response.text[:200]}")
            raise Exception(f"Erreur HTTP {response.status_code}")
        
        result = response.json()
        ollama_response = result.get("response", "")
        
        if not ollama_response:
            raise Exception("Réponse vide d'Ollama")
            
        logging.info(f"Réponse Ollama reçue: {len(ollama_response)} caractères")
        return ollama_response
        
    except requests.exceptions.ConnectionError:
        logging.error("Erreur de connexion à Ollama")
        raise Exception("Service Ollama indisponible")
    except requests.exceptions.Timeout:
        logging.error("Timeout Ollama")
        raise Exception("Timeout du service d'analyse")
    except Exception as e:
        logging.error(f"Erreur Ollama: {str(e)}")
        raise e
    
#Résumé généré pour les actus dans le quiz
def generate_summary_with_ollama(article_content):
    import re  # au cas où on veut faire du nettoyage avec regex

    prompt = (
        "Résume le texte suivant en **3 phrases maximum**, de manière claire et factuelle. "
        "Ne commence PAS par 'Voici un résumé' ou 'Je ne peux pas' ou 'Je peux' ou 'Oui'. "
        "Donne DIRECTEMENT le contenu du résumé :\n\n"
        "Si le texte n'est pas fourni ou que tu n'arrive pas à faire de résumé, dis UNIQUEMENT: Résumé non disponible. Veuillez lire l'article complet. SANS RIEN AJOUTER D'AUTRE"
        f"{article_content}"
    )

    payload = {
        "model": "llama3.2",  # ou autre modèle dispo
        "prompt": prompt,
        "stream": False
    }

    try:
        response = requests.post("http://localhost:11434/api/generate", json=payload)
        if response.status_code == 200:
            texte = response.json().get("response", "").strip()

            # Nettoyage du début si Ollama n'a pas écouté 
            phrases_a_enlever = [
                "voici un résumé", "voici un résumé de l'article", "résumé de l'article", 
                "je ne peux pas", "je suis désolé", "je ne peux fournir"
            ]

            texte_clean = texte.lower()
            if any(p in texte_clean for p in phrases_a_enlever):
                # Trop vague ou inutilisable
                return "Résumé non disponible. Veuillez lire l'article complet."
            
            # Supprime les intros du genre "Voici un résumé neutre de l'article :"
            texte = re.sub(r"(?i)^voici.*?:\s*", "", texte)
            return texte.strip()

        else:
            print("Erreur lors de l'appel à Ollama")
            return "Résumé non disponible. Veuillez lire l'article complet."

    except Exception as e:
        print(f"Erreur : {e}")
        return "Résumé non disponible. Veuillez lire l'article complet."


#Envoie les réponses du quiz
def envoyer_a_ollama(reponses, user_id=None, comparison=False):
    """
    Envoie les réponses à l'API Ollama pour générer une analyse politique structurée
    """
    logging.info("=== DÉBUT ANALYSE OLLAMA ===")
    logging.info(f"Nombre de réponses reçues: {len(reponses)}")
    logging.info(f"Première réponse: {reponses[0] if reponses else 'AUCUNE'}")
    
    # CORRECTION: Condition plus stricte pour vérifier les réponses
    if not reponses or len(reponses) == 0:
        logging.error("ERREUR: Liste de réponses vide")
        return generate_fallback_analysis("Aucune réponse fournie")
    
    if len(reponses) == 1 and ("Pas de réponses disponibles" in reponses[0] or "non disponible" in reponses[0].lower()):
        logging.error("ERREUR: Réponses non disponibles")
        return generate_fallback_analysis("Réponses non disponibles")
    
    # CORRECTION: Vérifier la qualité des réponses
    reponses_valides = [r for r in reponses if r and r.strip() and ":" in r and len(r.strip()) > 10]
    if len(reponses_valides) < 2:
        logging.error(f"ERREUR: Pas assez de réponses valides ({len(reponses_valides)} sur {len(reponses)})")
        logging.error(f"Réponses reçues: {reponses}")
        return generate_fallback_analysis("Réponses insuffisantes ou invalides")
    
    # Construction du prompt AMÉLIORÉ
    base_prompt = f"""
Tu es un expert en science politique française. Analyse les réponses suivantes et génère une analyse politique précise.

RÉPONSES DU QUIZ ({len(reponses_valides)} réponses valides):
{chr(10).join(reponses_valides)}

INSTRUCTIONS STRICTES:
Génère une analyse avec EXACTEMENT ce format en adaptant avec les informations fournises:

1. Parti politique le plus proche:
[Nom précis d'un parti français existant] - [Description courte]

2. Orientation politique:
[Position sur l'axe gauche-centre-droite] - [Position sur l'axe libertaire-autoritaire]

3. Valeurs principales:
[3-5 valeurs séparées par des virgules]

4. Graphique ASCII:
```
    LIBERTAIRE
        |
GAUCHE--+--DROITE
        |
   AUTORITAIRE
     (X = votre position)
```

IMPORTANT:
- Utilise uniquement des partis français réels (LFI, PS, LREM, LR, RN, etc.)
- Sois précis et factuel
- Le graphique doit être simple et lisible
- Réponds en français uniquement
"""

    try:
        logging.info("Envoi de la requête à Ollama...")
        response = get_ollama_response(base_prompt)
        
        if not response or response.strip() == "":
            logging.error("ERREUR: Réponse vide d'Ollama")
            return generate_fallback_analysis("Réponse vide du service d'analyse")
        
        # CORRECTION: Vérification plus stricte de la réponse
        if "erreur" in response.lower() or "error" in response.lower():
            logging.error(f"ERREUR dans la réponse Ollama: {response}")
            return generate_fallback_analysis("Erreur du service d'analyse")
        
        # Vérifier que la réponse contient les sections essentielles
        sections_requises = ["Parti politique", "Orientation politique", "Valeurs principales", "Graphique ASCII"]
        sections_manquantes = [s for s in sections_requises if s not in response]
        
        if len(sections_manquantes) > 2:
            logging.error(f"ERREUR: Sections manquantes dans la réponse: {sections_manquantes}")
            logging.error(f"Réponse reçue: {response[:200]}...")
            return generate_enhanced_analysis(reponses_valides)
        
        logging.info("Analyse générée avec succès")
        return clean_ollama_response(response)
        
    except Exception as e:
        logging.error(f"EXCEPTION dans envoyer_a_ollama: {str(e)}")
        return generate_enhanced_analysis(reponses_valides)
    
#Nettoie et structure la réponse d'Ollama pour garantir un format cohérent    
def clean_ollama_response(response):
    # Vérifier si la réponse est une chaîne
    if not isinstance(response, str):
        return """1. Parti politique le plus proche:
Format de réponse invalide

2. Orientation politique: 
Indéterminé

3. Valeurs principales:
Erreur d'analyse

4. Graphique ASCII:
```
Erreur de génération
```"""
        
    # Si la réponse contient un message d'erreur, fournir une réponse par défaut
    if "erreur" in response.lower() or "error" in response.lower():
        return """1. Parti politique le plus proche:
Analyse impossible - Erreur technique détectée

2. Orientation politique: 
Non disponible suite à une erreur

3. Valeurs principales:
Service temporairement indisponible

4. Graphique ASCII:
```
Erreur: Graphique non généré
```"""
        
    # S'assurer que les sections essentielles sont présentes
    required_sections = [
        "Parti politique", 
        "Orientation politique", 
        "Valeurs principales", 
        "Graphique ASCII"
    ]
    
    # Vérifier si l'évolution d'opinion est présente (section optionnelle)
    has_evolution = "Évolution d'opinion" in response or "Evolution d'opinion" in response
    if has_evolution:
        required_sections.append("Évolution d'opinion")
    
    # Vérifier et réparer si nécessaire
    cleaned_response = response
    
    # Vérifier que la réponse contient bien les sections requises
    missing_sections = []
    for section in required_sections[:4]:  # Vérifier uniquement les 4 sections obligatoires
        if section not in cleaned_response:
            missing_sections.append(section)
    
    # Si des sections manquent, on génère une réponse formatée avec des sections "Non disponible"
    if missing_sections:
        logging.warning(f"Sections manquantes dans la réponse d'Ollama: {missing_sections}")
        
        # Générer une structure de base avec les sections manquantes
        base_response = ""
        
        # Vérifier si "Parti politique" est présent ou l'ajouter
        if "Parti politique" not in cleaned_response:
            base_response += "1. Parti politique le plus proche:\nNon disponible\n\n"
        
        # Vérifier si "Orientation politique" est présent ou l'ajouter
        if "Orientation politique" not in cleaned_response:
            base_response += "2. Orientation politique:\nNon disponible\n\n"
        
        # Vérifier si "Valeurs principales" est présent ou l'ajouter
        if "Valeurs principales" not in cleaned_response:
            base_response += "3. Valeurs principales:\nNon disponible\n\n"
        
        # Vérifier si "Graphique ASCII" est présent ou l'ajouter
        if "Graphique ASCII" not in cleaned_response:
            base_response += """4. Graphique ASCII:
```
  Analyse    
  insuffisante
  pour générer
  le graphique
```"""
        
        # Si aucune section n'est présente, retourner la réponse complète générée
        if len(missing_sections) == 4:
            return base_response
        
        # Sinon, essayer de fusionner avec la réponse existante
        # Ajouter d'abord les sections manquantes
        cleaned_response = base_response + "\n" + cleaned_response
    
    # Assurer le formatage des sections
    for i, section in enumerate(required_sections, 1):
        # Ajustement pour la section optionnelle d'évolution
        section_num = i
        if has_evolution and i == 5 and section == "Évolution d'opinion":
            section_num = 5
        elif has_evolution and i > 4 and section != "Évolution d'opinion":
            section_num = i
            
        # Vérifier si la section est présente avec le bon numéro
        if f"{section_num}. {section}" not in cleaned_response and section in cleaned_response:
            # Remplacer avec le bon format de numérotation
            pattern = re.compile(fr"(?:\d+\.?\s*)?{section}", re.IGNORECASE)
            cleaned_response = pattern.sub(f"{section_num}. {section}", cleaned_response, 1)
    
    # S'assurer que le graphique ASCII est bien formaté
    if "Graphique ASCII" in cleaned_response:
        # Vérifier si le graphique est formaté en bloc de code
        if "```" not in cleaned_response:
            # Ajouter des délimiteurs de bloc de code autour du graphique
            graph_pattern = re.compile(r"(4\. Graphique ASCII:.*?)(\n5\.|$)", re.DOTALL)
            match = graph_pattern.search(cleaned_response)
            if match:
                graph_text = match.group(1)
                graph_content = graph_text.split("\n", 1)[1] if "\n" in graph_text else ""
                replacement = f"4. Graphique ASCII:\n```\n{graph_content}\n```"
                if match.group(2):  # S'il y a une section 5
                    replacement += match.group(2)
                cleaned_response = graph_pattern.sub(replacement, cleaned_response)
            else:
                # Si le pattern n'a pas trouvé de correspondance, ajouter le bloc de code à la fin
                cleaned_response += "\n```\n```"
    
    return cleaned_response

#Génère une analyse basique mais réaliste basée sur les réponses, fonnction supplémentaire à envoyer_a_Ollama
def generate_enhanced_analysis(reponses):
    logging.info("Génération d'analyse de secours intelligente...")
    
    # Analyser les mots-clés dans les réponses pour déterminer l'orientation
    texte_complet = " ".join(reponses).lower()
    
    # Détection d'orientation basique
    mots_gauche = ["social", "égalité", "solidarité", "public", "redistribution", "travailleur"]
    mots_droite = ["sécurité", "économie", "entreprise", "tradition", "ordre", "mérite"]
    mots_centre = ["équilibre", "modéré", "pragmatique", "réforme", "dialogue"]
    
    score_gauche = sum(1 for mot in mots_gauche if mot in texte_complet)
    score_droite = sum(1 for mot in mots_droite if mot in texte_complet)
    score_centre = sum(1 for mot in mots_centre if mot in texte_complet)
    
    # Déterminer l'orientation
    if score_gauche > score_droite and score_gauche > score_centre:
        parti = "Parti Socialiste (PS)"
        orientation = "Centre-gauche - Sociale-démocrate"
        valeurs = "Justice sociale, Égalité, Solidarité, Services publics"
        position_graph = "gauche du centre"
    elif score_droite > score_gauche and score_droite > score_centre:
        parti = "Les Républicains (LR)"
        orientation = "Centre-droit - Libéral-conservateur"
        valeurs = "Sécurité, Économie de marché, Tradition, Mérite"
        position_graph = "droite du centre"
    else:
        parti = "Renaissance (LREM)"
        orientation = "Centre - Libéral-progressiste"
        valeurs = "Réforme, Équilibre, Innovation, Europe"
        position_graph = "centre"
    
    return f"""1. Parti politique le plus proche:
{parti} - Basé sur l'analyse de vos réponses politiques

2. Orientation politique:
{orientation}

3. Valeurs principales:
{valeurs}

4. Graphique ASCII:
```
    LIBERTAIRE
        |
GAUCHE--+--DROITE
        |
   AUTORITAIRE
     (X = position {position_graph})
```

Note: Analyse générée à partir de {len(reponses)} réponses."""


#fonction de 'secours': Génère une analyse de base en cas d'erreur
def generate_fallback_analysis(raison):
    return f"""1. Parti politique le plus proche:
Analyse en cours - Données insuffisantes actuellement

2. Orientation politique:
Non déterminé - Veuillez répondre à plus de questions

3. Valeurs principales:
En cours d'analyse

4. Graphique ASCII:
```
    LIBERTAIRE
        |
GAUCHE--+--DROITE
        |
   AUTORITAIRE
   (Position à déterminer)
```

Raison: {raison}
Conseil: Répondez à plus de questions du quiz pour une analyse précise."""
    
# Fonction optimisée pour être mise en cache une seule fois par jour
@cache.memoize(timeout=86400)  # 24 heures en secondes (86400 = 24*60*60)
def fetch_actualites_cached():
    """Récupère et trie les actualités par catégorie avec mise en cache."""
    print("=== EXÉCUTION DE FETCH_ACTUALITES_CACHED ===")
    print(f"Cette fonction ne devrait s'exécuter qu'une fois toutes les 24h - {datetime.now()}")
    
    resume_actualites = defaultdict(list)
    categories = {
        'Affaires internationales': ['international', 'monde', 'étranger', 'diplomatie', 'conflit'],
        'Économie': ['économie', 'finance', 'marché', 'entreprise', 'emploi', 'croissance'],
        'Environnement': ['environnement', 'écologie', 'climat', 'énergie', 'pollution', 'biodiversité'],
        'Éducation': ['éducation', 'école', 'université', 'enseignement', 'formation', 'étudiant'],
        'Santé': ['santé', 'médical', 'hôpital', 'maladie', 'vaccin', 'bien-être'],
        'Justice': ['justice', 'droit', 'loi', 'tribunal', 'crime', 'sécurité'],
        'Culture': ['culture', 'art', 'musique', 'cinéma', 'livre', 'exposition'],
        'Technologie': ['technologie', 'numérique', 'internet', 'intelligence artificielle', 'innovation', 'science']
    }
    to_date = datetime.now().strftime('%Y-%m-%d')
    from_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
    article_titles = set()
    
    for category, keywords in categories.items():
        print(f"\n--- Catégorie : {category} ---")
        try:
            # 1. Récupérer les articles - augmenter page_size pour avoir plus d'articles
            response = newsapi.get_everything(
                sources='le-monde',
                from_param=from_date,
                to=to_date,
                language='fr',
                sort_by='publishedAt',
                page_size=10,  # Augmenté de 5 à 10 pour avoir plus d'articles
                page=1,
                q=category
            )
            if response.get('status') == 'ok':
                articles = response.get('articles', [])
                print(f"Nombre d'articles récupérés pour {category}: {len(articles)}")
                # 2. Filtrer et trier les articles
                for article in articles:
                    title = article.get('title', 'Pas de titre')
                    content = article.get('content', '') or article.get('description', '') or ''
                    url = article.get('url', '')
                    cleaned_content = nettoyer_contenu(content)
                    combined_text = f"{title.lower()} {cleaned_content.lower()}"
                    if any(keyword in combined_text for keyword in keywords) and title not in article_titles:
                        # 4. Utiliser Ollama pour générer le résumé
                        prompt = f"""
                            Résume le texte suivant en **3 phrases maximum**, de manière claire et factuelle. 
                            Ne commence PAS par 'Voici un résumé' ou 'Je ne peux pas' ou 'Je peux' ou 'Oui'. 
                            Donne DIRECTEMENT le contenu du résumé :
                            Si le texte n'est pas fourni ou que tu n'arrive pas à faire de résumé, dis UNIQUEMENT: Résumé non disponible. Veuillez lire l'article complet. SANS RIEN AJOUTER D'AUTRE
                            {title} - {cleaned_content}
                        """
                        payload = {
                            "model": "llama3.2",
                            "prompt": prompt,
                            "stream": False
                        }
                        ollama_response = requests.post("http://localhost:11434/api/generate", json=payload)
                        ollama_response.raise_for_status()
                        summary = ollama_response.json().get("response", "Résumé non disponible").strip()
                        summary = re.sub(r"(?i)^voici.*?:\\s*", "", summary).strip()
                        resume_actualites[category].append({"title": title, "summary": summary, "url": url})
                        article_titles.add(title)
                
                if not resume_actualites[category]:
                    resume_actualites[category].append({"title": "Aucune actualité pertinente", "summary": "Aucune actualité pertinente trouvée pour le moment.", "url": ""})
            else:
                print(f"Erreur NewsAPI : {response.get('code')} - {response.get('message')}")
                resume_actualites[category].append({"title": "Erreur", "summary": f"Erreur de NewsAPI : {response.get('message')}", "url": ""})
        except requests.exceptions.RequestException as e:
            print(f"Erreur lors de la requête : {e}")
            resume_actualites[category].append({"title": "Erreur", "summary": f"Erreur de requête : {e}", "url": ""})
        except json.JSONDecodeError:
            print("Erreur de décodage JSON")
            resume_actualites[category].append({"title": "Erreur", "summary": "Erreur de format JSON", "url": ""})
        except Exception as e:
            print(f"Erreur inattendue : {e}")
            resume_actualites[category].append({"title": "Erreur", "summary": f"Erreur inattendue : {e}", "url": ""})
    
    return dict(resume_actualites)  # Convertir en dict normal pour la mise en cache

# Ancienne fonction maintenue pour compatibilité
def fetch_actualites():
    """Récupère les actualités depuis le cache ou lance la récupération si nécessaire."""
    return fetch_actualites_cached()  # Cette fonction utilise automatiquement le cache

#Route pour se connecter
@app.route('/login', methods=['GET', 'POST']) 
def login():
    if request.method == 'POST':
        email = request.form['email'].strip()  
        password = request.form['password'].strip()  
        user = User.query.filter_by(email=email).first()
        if user:
            print(f"Utilisateur trouvé : {user.username}")
            if user.check_password(password):
                session['user_id'] = user.id
                return redirect(url_for('dashboard'))
            else:
                print("Mot de passe incorrect")
                flash('Email ou mot de passe incorrect', 'error')
        else:
            print("Utilisateur non trouvé")
            flash('Email ou mot de passe incorrect', 'error')
    return render_template('login.html')

#Route pour se déconnecter
@app.route('/logout') 
def logout():
    session.clear()
    flash("Déconnecté avec succès", "success")
    return redirect(url_for('home'))

#La route du quiz
@app.route('/quiz')
def quiz():
    user_id = session.get('user_id')
    if not user_id:
        flash("Veuillez vous connecter pour accéder au quiz.", "warning")
        return redirect(url_for('login'))
    
    # CORRECTIF: Vérifier si l'utilisateur a un quiz en cours et le rediriger vers sa dernière catégorie
    if session.get('quiz_en_cours') and session.get('derniere_categorie'):
        # Restaurer les catégories vides si elles étaient sauvegardées
        if 'categories_vides_sauvegardees' in session:
            session['categories_vides'] = session.get('categories_vides_sauvegardees', [])
            
        categorie = session.get('derniere_categorie')
        # Ne pas afficher de message "quiz réinitialisé" - c'est une reprise
        return redirect(url_for('quiz_par_categorie', categorie=categorie))
        
    categories = ['Affaires internationales','Économie', 'Environnement', 'Éducation', 'Santé', 'Justice', 'Culture', 'Technologie']
    completed_categories = []  # Liste pour suivre les catégories complétées
    
    # Vérification de chaque catégorie
    for categorie in categories:
        categorie_normalisee = categorie.lower()  # Convertir la catégorie en minuscule
        questions = Question.query.filter(Question.categorie.ilike(categorie_normalisee)).filter_by(valide=True).all()
        if not questions:
            continue  # Si la catégorie n'a pas de questions valides, on passe à la suivante
        question_ids = [q.id for q in questions]
        # Vérifie si l'utilisateur a déjà répondu à ces questions
        reponses_existantes = Reponse.query.filter(
            Reponse.user_id == user_id,
            Reponse.question_id.in_(question_ids)
        ).count()
        # Si l'utilisateur a répondu à toutes les questions de cette catégorie, on la marque comme complétée
        if reponses_existantes == len(questions):
            completed_categories.append(categorie)
    # Si l'utilisateur a déjà répondu à toutes les catégories, on le redirige vers le dashboard
    if len(completed_categories) == len(categories):
        flash("Vous avez déjà répondu à toutes les catégories du quiz !", "info")
        return redirect(url_for('dashboard'))
    # Sinon, on redirige vers la première catégorie non complétée
    for categorie in categories:
        if categorie.lower() not in [cat.lower() for cat in completed_categories]:  # Comparaison insensible à la casse
            # On redirige vers la première catégorie non complétée, mais uniquement si elle n'est pas déjà ouverte
            if request.path != url_for('quiz_par_categorie', categorie=categorie):
                return redirect(url_for('quiz_par_categorie', categorie=categorie))
    # En cas d'erreur, si on ne peut pas déterminer où rediriger
    flash("Erreur, toutes les catégories sont complétées ou il y a un problème. Veuillez vérifier.", "danger")
    return redirect(url_for('dashboard'))

#Route pour s'inscrire
@app.route('/register', methods=['GET', 'POST']) 
def register():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        password_confirm = request.form['password_confirm']
        interets = request.form.getlist('interets')
        # Vérification que les mots de passe sont identiques
        if password != password_confirm:
            flash('Les mots de passe ne correspondent pas', 'error')
            return redirect(url_for('register'))
        # Hashage du mot de passe avant de le sauvegarder
        password_hash = generate_password_hash(password)
        # Création de l'utilisateur dans la base de données
        try:
            user = User(username=username, email=email, password_hash=password_hash, interets=','.join(interets))
            db.session.add(user)
            db.session.commit()
            flash('Inscription réussie!', 'success')
            return redirect(url_for('reinitialiser_quiz'))
        except Exception as e:
            db.session.rollback()  # En cas d'erreur, rollback
            flash(f'Une erreur est survenue : {e}', 'error')
            return redirect(url_for('register'))
    return render_template('register.html')

#Page d'accueil utilisant la mise en cache 
@app.route('/')
def home():
    print("=== Chargement de la page d'accueil ===")
    # Utilisez simplement la fonction mise en cache qui ne s'exécutera réellement qu'une fois par jour
    resume_actualites = fetch_actualites_cached()
    return render_template('index.html', resume_actualites=resume_actualites)

#Route pour réinitialiser le quiz en cas de besoin
@app.route('/reinitialiser_quiz')
def reinitialiser_quiz():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
    
    try:
        # On va utiliser une approche qui préserve l'historique des réponses
        with db.session.begin_nested():  # Créer un point de sauvegarde
            # 1. D'abord, récupérer toutes les réponses actives actuelles
            reponses_actives = Reponse.query.filter_by(user_id=user_id, est_active=True).all()
            
            # 2. Pour chaque réponse active, la désactiver tout en préservant l'historique
            for reponse in reponses_actives:
                # D'abord vérifier si cette question a déjà des réponses inactives
                if Reponse.query.filter_by(
                    user_id=user_id,
                    question_id=reponse.question_id,
                    est_active=False
                ).count() > 0:
                    # Si oui, on supprime l'ancienne version inactive
                    Reponse.query.filter_by(
                        user_id=user_id,
                        question_id=reponse.question_id,
                        est_active=False
                    ).delete()
                
                # Maintenant désactiver la réponse active
                reponse.est_active = False
                reponse.date_modification = datetime.utcnow()
            
            # 3. Récupérer l'analyse politique actuelle
            current_analysis = AnalysePolitique.query.filter_by(user_id=user_id, is_current=True).first()
            
            # 4. Désactiver l'analyse courante
            if current_analysis:
                current_analysis.is_current = False
        
            # Commit des changements dans la transaction imbriquée
            # db.session.commit() - pas nécessaire avec begin_nested()
        
        # Valider définitivement les changements
        db.session.commit()
        
        # 5. Supprimer l'analyse de la session
        if 'analyse' in session:
            session.pop('analyse', None)
        
        # Flag pour quiz de suivi
        session['quiz_suivi'] = True
        
        flash("Votre quiz a été réinitialisé. Vous pouvez maintenant refaire le quiz pour voir l'évolution de vos opinions!", "info")
        
        # Redirection vers la première catégorie disponible
        premiere_categorie = 'Affaires internationales'
        categorie_normalisee = premiere_categorie.lower()
        
        # Vérifier qu'il y a des questions valides
        questions_disponibles = Question.query.filter(
            Question.categorie.ilike(categorie_normalisee),
            Question.valide == True
        ).first()
        
        if questions_disponibles:
            # Essayons la redirection directe
            return redirect(f'/quiz/{premiere_categorie}')
        else:
            # Chercher une autre catégorie
            categories = ['Économie', 'Environnement', 'Éducation', 'Santé', 'Justice', 'Culture', 'Technologie']
            for cat in categories:
                cat_norm = cat.lower()
                questions = Question.query.filter(
                    Question.categorie.ilike(cat_norm),
                    Question.valide == True
                ).first()
                if questions:
                    # Redirection directe
                    return redirect(f'/quiz/{cat}')
            
            flash("Aucune question disponible dans le quiz actuellement.", "warning")
            return redirect(url_for('dashboard'))
    
    except Exception as e:
        db.session.rollback()
        print(f"Exception dans reinitialiser_quiz: {str(e)}")
        flash(f"Une erreur s'est produite: {str(e)}", "error")
        return redirect(url_for('dashboard'))

def nettoyer_texte(texte):
    """Nettoie le contenu d’un article pour enlever les parties sensibles ou inutiles."""
    texte = re.sub(r"\n{2,}", "\n", texte)  # supprime les lignes vides multiples
    texte = re.sub(r"(⚠️|🔞|🛑).*", "", texte)  # supprime certains emojis sensibles
    texte = texte.strip()
    return texte


# Pour afficher l'analyse finale
@app.route('/quiz_fin', methods=['GET'])
def afficher_quiz_fin():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    # Vérifie si une analyse existe déjà
    analyse = session.get('analyse')

    # Si pas encore générée, on la crée ici
    if not analyse:
        reponses = get_reponses_utilisateur(user_id)
        analyse = envoyer_a_ollama(reponses)
        session['analyse'] = analyse or "⚠️ Une erreur est survenue lors de l'analyse."

    return render_template("quiz_fin.html", analyse=analyse)

#Rassemble toutes les fonctions pour obtenir le résultat du quiz
@app.route('/quiz_fin', methods=['POST'])
def generer_analyse_quiz_fin():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    logging.info(f"=== GÉNÉRATION ANALYSE POUR USER {user_id} ===")

    # Vérifier si c'est un quiz de suivi
    is_quiz_suivi = session.get('quiz_suivi', False)
    
    # DIAGNOSTIC: Vérifier les réponses dans la base de données
    toutes_reponses = Reponse.query.filter_by(user_id=user_id).all()
    reponses_actives = Reponse.query.filter_by(user_id=user_id, est_active=True).all()
    reponses_repondues = Reponse.query.filter_by(user_id=user_id, est_active=True, etat="répondu").all()
    
    logging.info("DIAGNOSTIC DB:")
    logging.info(f"- Total réponses: {len(toutes_reponses)}")
    logging.info(f"- Réponses actives: {len(reponses_actives)}")
    logging.info(f"- Réponses répondues actives: {len(reponses_repondues)}")
    
    # Récupérer les réponses de l'utilisateur
    reponses = get_reponses_utilisateur(user_id, include_history=is_quiz_suivi)
    logging.info(f"Réponses récupérées pour analyse: {len(reponses)}")
    
    if not reponses or len(reponses) == 0:
        logging.error("ERREUR CRITIQUE: Aucune réponse récupérée")
        flash("Erreur: Aucune réponse trouvée. Veuillez refaire le quiz.", "error")
        return redirect(url_for('quiz'))

    # Vérifier s'il existe une analyse précédente pour comparer
    has_previous_analysis = AnalysePolitique.query.filter_by(user_id=user_id, is_current=False).count() > 0
    
    # Générer l'analyse
    comparison = has_previous_analysis and is_quiz_suivi
    
    try:
        analyse = envoyer_a_ollama(reponses, user_id=user_id, comparison=comparison)
        
        if not analyse or "Non disponible" in analyse:
            logging.error("ERREUR: Analyse non générée correctement")
            # Forcer la génération d'une analyse de secours
            analyse = generate_enhanced_analysis([r for r in reponses if ":" in r])
        
        # Stocker l'analyse dans la session
        session['analyse'] = analyse
        
        # Sauvegarder en base de données
        try:
            # Désactiver les analyses précédentes
            AnalysePolitique.query.filter_by(user_id=user_id, is_current=True).update({AnalysePolitique.is_current: False})
            db.session.commit()
            
            # Créer nouvelle analyse
            nouvelle_analyse = AnalysePolitique(
                user_id=user_id,
                analyse_text=analyse,
                is_current=True,
                date_creation=datetime.utcnow()
            )
            db.session.add(nouvelle_analyse)
            db.session.commit()
            
            logging.info("Analyse sauvegardée en DB avec succès")
        except Exception as db_error:
            logging.error(f"Erreur sauvegarde DB: {str(db_error)}")
            db.session.rollback()
        
        # Nettoyer la session
        session.pop('quiz_suivi', None)
        flash("Votre analyse politique est prête !", "success")
        
    except Exception as e:
        logging.error(f"ERREUR CRITIQUE génération analyse: {str(e)}")
        # Analyse de secours absolue
        analyse = generate_fallback_analysis(f"Erreur technique: {str(e)}")
        session['analyse'] = analyse
        flash("Analyse générée avec des données limitées.", "warning")

    return redirect(url_for('afficher_quiz_fin'))

#Route de debug pour vérifier les réponses utilisateur
@app.route('/debug_user_responses')
def debug_user_responses():
    user_id = session.get('user_id')
    if not user_id:
        return "Pas connecté"
    
    # Récupérer toutes les infos
    toutes_reponses = Reponse.query.filter_by(user_id=user_id).all()
    
    debug_info = f"""
DIAGNOSTIC UTILISATEUR {user_id}:

TOTAL RÉPONSES: {len(toutes_reponses)}

DÉTAIL DES RÉPONSES:
"""
    
    for i, rep in enumerate(toutes_reponses):
        question = Question.query.get(rep.question_id)
        question_text = question.texte[:50] if question else "Question non trouvée"
        debug_info += f"""
{i+1}. ID: {rep.id}
   Question: {question_text}...
   Réponse: {rep.texte[:50]}...
   Est Active: {rep.est_active}
   État: {rep.etat}
   Date: {rep.date_creation}
   
"""
    
    # Tester la fonction get_reponses_utilisateur
    reponses_formatees = get_reponses_utilisateur(user_id)
    debug_info += f"""
RÉPONSES FORMATÉES ({len(reponses_formatees)}):
{chr(10).join(reponses_formatees[:5])}  # Première 5 seulement
"""
    
    return f"<pre>{debug_info}</pre>"

#Les différentes pages des catégories du quiz
@app.route('/quiz/<categorie>', methods=['GET', 'POST'])
def quiz_par_categorie(categorie):
    user_id = session.get('user_id')
    categories = ['Affaires internationales', 'Économie', 'Environnement', 'Éducation', 'Santé', 'Justice', 'Culture', 'Technologie']
    print(f"Catégorie reçue: '{categorie}'")
    if not user_id:
        return redirect(url_for('login'))

    categorie_normalisee = categorie.lower()

    # Déterminer si c'est un quiz de suivi ou premier quiz
    is_quiz_suivi = session.get('quiz_suivi', False)
    
    # --- Récupération des questions pour la catégorie ---
    # Récupérer les réponses de cette session (actives)
    reponses_actives = Reponse.query.filter_by(user_id=user_id, est_active=True).all()
    questions_repondues_active_ids = [r.question_id for r in reponses_actives if r.etat == "répondu"]
    questions_passees_active_ids = [r.question_id for r in reponses_actives if r.etat == "passé"]
    
    # En cas de quiz de suivi, récupérer toutes les questions précédemment répondues
    if is_quiz_suivi:
        precedentes_reponses = Reponse.query.filter_by(
            user_id=user_id, 
            est_active=False,
            etat="répondu"
        ).all()
        
        questions_precedentes_ids = [r.question_id for r in precedentes_reponses]
    else:
        questions_precedentes_ids = []
    
    # Création d'une liste de priorité pour éviter de poser les mêmes questions
    if is_quiz_suivi:
        # Pour le quiz de suivi, on donne la priorité à des nouvelles questions
        questions_evitees_ids = questions_repondues_active_ids + questions_passees_active_ids
        questions_a_eviter_prioritairement = questions_precedentes_ids
    else:
        # Pour le premier quiz, pas de contraintes spécifiques
        questions_evitees_ids = questions_repondues_active_ids + questions_passees_active_ids
        questions_a_eviter_prioritairement = []

    # D'abord, priorité aux questions totalement nouvelles
    # Si c'est un quiz de suivi, on évite les questions déjà répondues dans les sessions précédentes
    base_query = Question.query.filter(
        Question.categorie.ilike(categorie_normalisee),
        Question.valide == True,
        ~Question.id.in_(questions_evitees_ids)
    )
    
    if is_quiz_suivi and questions_a_eviter_prioritairement:
        # Si suivi, éviter d'abord les questions déjà répondues dans les précédentes sessions
        base_query = base_query.filter(~Question.id.in_(questions_a_eviter_prioritairement))
    
    questions = base_query.order_by(Question.id.desc()).limit(5).all()
    
    # S'il n'y a pas assez de nouvelles questions, inclure quelques questions des sessions précédentes
    # mais différentes pour montrer l'évolution des opinions
    if len(questions) < 3 and is_quiz_suivi:
        questions_deja_recup_ids = [q.id for q in questions]
        questions_supp = Question.query.filter(
            Question.categorie.ilike(categorie_normalisee),
            Question.valide == True,
            ~Question.id.in_(questions_evitees_ids + questions_deja_recup_ids),
            Question.id.in_(questions_precedentes_ids)  # Questions des sessions précédentes
        ).order_by(func.random()).limit(2).all()
        
        questions.extend(questions_supp)
    
    # Si toujours pas assez de questions, prendre des questions complètement aléatoires
    if len(questions) < 3:  # Minimum 3 questions
        questions_deja_recup_ids = [q.id for q in questions]
        questions_aleatoires = Question.query.filter(
            Question.categorie.ilike(categorie_normalisee),
            Question.valide == True,
            ~Question.id.in_(questions_deja_recup_ids + questions_evitees_ids)
        ).order_by(func.random()).limit(5 - len(questions)).all()
        
        questions.extend(questions_aleatoires)

    # ⚠️ Si aucune question dans cette catégorie, passer à la suivante
    if not questions:
        # CORRECTIF 1: Stocker les catégories vides dans la session pour éviter les boucles
        if 'categories_vides' not in session:
            session['categories_vides'] = []
        
        # Ajouter cette catégorie aux catégories vides
        if categorie.lower() not in session['categories_vides']:
            session['categories_vides'].append(categorie.lower())
            
        # Vérifier si toutes les catégories sont vides
        if len(session['categories_vides']) >= len(categories):
            # Toutes les catégories ont été vérifiées et sont vides
            flash("Nous n'avons pas trouvé de questions disponibles pour le moment.", "warning")
            # Nettoyer la session
            session.pop('categories_vides', None)
            return redirect(url_for('afficher_quiz_fin'))
            
        # Trouver la prochaine catégorie non vide
        for i in range(len(categories)):
            next_index = (categories.index(categorie) + i + 1) % len(categories)
            next_cat = categories[next_index]
            
            # Vérifier si cette catégorie n'est pas déjà marquée comme vide
            if next_cat.lower() in session['categories_vides']:
                continue
                
            cat_normalisee = next_cat.lower()
            
            # Vérifier s'il existe des questions pour cette catégorie que l'utilisateur n'a pas encore répondues
            q_exist = Question.query.filter(
                Question.categorie.ilike(cat_normalisee), 
                Question.valide == True,
                ~Question.id.in_(questions_evitees_ids)  # N'afficher que des questions pas encore traitées
            ).first()
            
            if q_exist:
                return redirect(url_for('quiz_par_categorie', categorie=next_cat))
            else:
                # Marquer cette catégorie comme vide aussi
                session['categories_vides'].append(next_cat.lower())
        
        # Si toutes les catégories restantes sont vides, rediriger vers la fin du quiz
        flash("Vous avez terminé toutes les catégories du quiz!", "success")
        # Nettoyer la session
        session.pop('categories_vides', None)
        return redirect(url_for('afficher_quiz_fin'))

    # ----- POST -----
    if request.method == 'POST':
        has_response = False  # Pour vérifier si au moins une réponse a été donnée
        
        # CORRECTIF: Si l'utilisateur ne fait que sauvegarder sans avoir répondu à des nouvelles questions, 
        # on ne doit pas exiger une réponse
        sauvegarder_seulement = 'sauvegarder' in request.form and not 'suivant' in request.form and not 'terminer_quiz' in request.form
        
        for question in questions:
            passer = request.form.get(f"passer_{question.id}")
            user_answer = request.form.get(f"question_{question.id}")

            if passer:
                save_answer(user_id, question.id, "", etat="passé")
                has_response = True
            elif user_answer:
                save_answer(user_id, question.id, user_answer, etat="répondu")
                has_response = True
            else:
                # Ne rien faire si aucune réponse donnée pour cette question
                pass

        # Ne vérifier les réponses que si l'utilisateur ne fait pas juste sauvegarder
        if not has_response and not sauvegarder_seulement:
            flash("Veuillez répondre à au moins une question avant de continuer.", "warning")
            return redirect(url_for('quiz_par_categorie', categorie=categorie))

        if 'sauvegarder' in request.form:
            # CORRECTIF: Marquer correctement le quiz comme étant en cours dans la session
            session['quiz_en_cours'] = True
            # Sauvegarder dans la session la dernière catégorie visitée pour la reprise
            session['derniere_categorie'] = categorie
            # Sauvegarder aussi la liste des catégories vides si elle existe
            if 'categories_vides' in session:
                session['categories_vides_sauvegardees'] = session['categories_vides']
            
            flash("Vos réponses ont été sauvegardées. Vous pouvez reprendre plus tard.", "info")
            return redirect(url_for('dashboard'))

        # Ce message ne s'affiche que si l'utilisateur continue le quiz, pas lorsqu'il sauvegarde
        if 'suivant' in request.form and has_response:
            flash("Réponses enregistrées avec succès.", "success")

        # Vérifier si l'utilisateur a explicitement demandé à terminer le quiz
        if 'terminer_quiz' in request.form:
            # CORRECTIF 2: Nettoyer la session avant de rediriger vers la fin
            if 'categories_vides' in session:
                session.pop('categories_vides', None)
            if 'quiz_en_cours' in session:
                session.pop('quiz_en_cours', None)
            if 'derniere_categorie' in session:
                session.pop('derniere_categorie', None)
            if 'categories_vides_sauvegardees' in session:
                session.pop('categories_vides_sauvegardees', None)
                
            flash("Vous avez décidé de terminer le quiz ! Voici votre analyse.", "success")
            return redirect(url_for('afficher_quiz_fin'))

        # --- CORRECTION 3: Gestion améliorée de la recherche de la prochaine catégorie ---
        # Initialiser ou récupérer le tableau des catégories vides s'il existe déjà
        categories_vides = session.get('categories_vides', [])
        categories_vides.append(categorie.lower())  # Marquer cette catégorie comme "traitée"
        session['categories_vides'] = categories_vides
        
        # Si toutes les catégories ont été traitées, on a fini le quiz
        if len(categories_vides) >= len(categories):
            session.pop('categories_vides', None)  # Nettoyer la session
            if 'quiz_en_cours' in session:
                session.pop('quiz_en_cours', None)
            if 'derniere_categorie' in session:
                session.pop('derniere_categorie', None)
            if 'categories_vides_sauvegardees' in session:
                session.pop('categories_vides_sauvegardees', None)
                
            flash("Vous avez terminé toutes les catégories du quiz !", "success")
            return redirect(url_for('afficher_quiz_fin'))
        
        # Recherche de la prochaine catégorie avec des questions non répondues
        current_index = categories.index(categorie)
        
        # Parcourir les catégories dans l'ordre (commençant après la catégorie actuelle)
        for i in range(1, len(categories) + 1):  # +1 pour pouvoir vérifier toutes les catégories
            next_index = (current_index + i) % len(categories)
            next_cat = categories[next_index]
            next_cat_lower = next_cat.lower()
            
            # Ne pas revisiter les catégories déjà traitées
            if next_cat_lower in categories_vides:
                continue
            
            # Vérifier s'il reste des questions non répondues dans cette catégorie
            questions_non_repondues = Question.query.filter(
                Question.categorie.ilike(next_cat_lower),
                Question.valide == True,
                ~Question.id.in_(questions_repondues_active_ids + questions_passees_active_ids)
            ).first()
            
            if questions_non_repondues:
                # Trouver une catégorie avec des questions non répondues
                return redirect(url_for('quiz_par_categorie', categorie=next_cat))
            else:
                # Marquer cette catégorie comme traitée aussi
                categories_vides.append(next_cat_lower)
                session['categories_vides'] = categories_vides
        
        # Si on a vérifié toutes les catégories et qu'il n'y a plus de questions non répondues
        session.pop('categories_vides', None)  # Nettoyer la session
        if 'quiz_en_cours' in session:
            session.pop('quiz_en_cours', None)
        if 'derniere_categorie' in session:
            session.pop('derniere_categorie', None)
        if 'categories_vides_sauvegardees' in session:
            session.pop('categories_vides_sauvegardees', None)
            
        flash("Vous avez terminé toutes les catégories du quiz !", "success")
        return redirect(url_for('afficher_quiz_fin'))

    # ----- GET : suite -----
    articles = [question.article for question in questions if question.article]

    summaries = []
    for article in articles:
        try:
            if article and hasattr(article, "content"):
                cleaned = nettoyer_texte(article.content)
                summaries.append(generate_summary_with_ollama(cleaned))
            else:
                summaries.append("⚠️ Article sans contenu")
        except Exception as e:
            summaries.append(f"⚠️ Erreur de résumé : {str(e)}")

    # Déterminer la prochaine catégorie non complétée
    next_category = None
    current_index = categories.index(categorie)
    
    # CORRECTIF 4: Utiliser le tableau des catégories vides pour trouver la vraie prochaine catégorie
    categories_vides = session.get('categories_vides', [])
    if categorie.lower() not in categories_vides:
        categories_vides.append(categorie.lower())  # Ajouter la catégorie actuelle
    
    # Parcourir les catégories pour trouver la prochaine non vide
    for i in range(1, len(categories)):
        next_index = (current_index + i) % len(categories)
        next_cat = categories[next_index]
        next_cat_lower = next_cat.lower()
        
        # Sauter les catégories déjà traitées
        if next_cat_lower in categories_vides:
            continue
            
        # Vérifier s'il existe des questions non répondues pour cette catégorie
        q_exist = Question.query.filter(
            Question.categorie.ilike(next_cat_lower),
            Question.valide == True,
            ~Question.id.in_(questions_repondues_active_ids + questions_passees_active_ids)
        ).first()
        
        if q_exist:
            next_category = next_cat
            break

    return render_template(
        "quiz.html",
        categorie=categorie,
        questions=questions,
        articles=articles,
        summaries=summaries,
        next_category=next_category,
        zip=zip,
        is_quiz_suivi=is_quiz_suivi
    )

#Pour reprendre le quiz en cas de "Sauvegarder et continuer plus tard"
@app.route('/reprendre_quiz')
def reprendre_quiz():
    user_id = session.get('user_id')
    if not user_id:
        flash("Veuillez vous connecter pour accéder au quiz.", "warning")
        return redirect(url_for('login'))
        
    # Vérifier si un quiz est en cours avec les informations de session nécessaires
    if session.get('quiz_en_cours') and session.get('derniere_categorie'):
        # Récupérer la dernière catégorie et vérifier qu'elle est valide
        categorie = session.get('derniere_categorie')
        categories = ['Affaires internationales', 'Économie', 'Environnement', 'Éducation', 'Santé', 'Justice', 'Culture', 'Technologie']
        
        # Vérifier si la catégorie est dans la liste des catégories valides
        if categorie in categories:
            # Restaurer les catégories vides si elles étaient sauvegardées
            if 'categories_vides_sauvegardees' in session:
                session['categories_vides'] = session.get('categories_vides_sauvegardees', [])
                
            flash("Reprise du quiz en cours...", "info")
            return redirect(url_for('quiz_par_categorie', categorie=categorie))
        else:
            # Si la catégorie n'est pas valide, rediriger vers le début du quiz
            session.pop('quiz_en_cours', None)
            session.pop('derniere_categorie', None)
            flash("Impossible de reprendre le quiz. Début d'un nouveau quiz...", "info")
            return redirect(url_for('quiz'))
    else:
        # Si pas de quiz en cours, commencer un nouveau
        flash("Début d'un nouveau quiz...", "info")
        return redirect(url_for('quiz'))
    
#Page de compte de l'utilisateur
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    user = User.query.get(user_id)
    if not user:
        flash("Utilisateur non trouvé.", "error")
        return redirect(url_for('logout'))
        
    interets = user.interets.split(',') if user.interets else []
    
    # Récupération des actualités du cache
    resume_actualites = fetch_actualites_cached()
    
    # Si jamais le cache contient une string JSON, on la parse
    if isinstance(resume_actualites, str):
        try:
            resume_actualites = json.loads(resume_actualites)
        except Exception as e:
            print(f"Erreur lors du parsing JSON de resume_actualites: {e}")
            resume_actualites = {}
            
    # Filtrage par préférences utilisateur
    filtered_actualites = {}
    if interets:
        # Si l'utilisateur a des intérêts, ne garder que les catégories correspondantes
        for categorie, articles in resume_actualites.items():
            if categorie in interets:
                filtered_actualites[categorie] = articles
    else:
        # Si l'utilisateur n'a pas encore choisi ses intérêts, on affiche tout
        filtered_actualites = resume_actualites
    
    # Récupérer l'analyse politique actuelle
    analyse_politique = AnalysePolitique.query.filter_by(user_id=user_id, is_current=True).first()
    
    # Si aucune analyse n'est marquée comme current mais qu'il y a des analyses, prendre la plus récente
    if not analyse_politique:
        analyse_politique = AnalysePolitique.query.filter_by(user_id=user_id).order_by(AnalysePolitique.date_creation.desc()).first()
        if analyse_politique:
            # Marquer cette analyse comme current
            analyse_politique.is_current = True
            db.session.commit()
    
    # Récupérer l'analyse de la session OU de la base de données
    analyse_brute = analyse_politique.analyse_text if analyse_politique else session.get('analyse', '')
    # Vérifier que l'analyse est bien dans la session également (pour l'affichage immédiat)
    if analyse_politique and not session.get('analyse'):
        session['analyse'] = analyse_politique.analyse_text
        
    # Variables pour stocker les parties extraites de l'analyse
    analyse_parti = ""           # Juste le nom du parti
    analyse_parti_complet = ""   # La description complète du parti
    analyse_orientation = ""     # Juste la position (ex: gauche-libertaire)
    analyse_orientation_complete = ""  # La description complète de l'orientation
    analyse_valeurs = []         # Liste des valeurs clés
    analyse_valeurs_complete = ""  # Description complète des valeurs
    analyse_graphique = ""       # Le graphique ASCII
    analyse_evolution = ""       # Section évolution d'opinion (si présente)
    
    # Extraire les différentes parties de l'analyse
    if analyse_brute:
        lines = analyse_brute.split("\n")
        bloc, current = None, []
        
        for line in lines:
            if "1. Parti politique" in line:
                bloc, current = "parti", []
            elif "2. Orientation politique" in line:
                if bloc == "parti": 
                    analyse_parti_complet = "\n".join(current).strip()
                    # Extraire juste le nom du parti (première partie avant le tiret ou la première phrase)
                    if analyse_parti_complet:
                        if "-" in analyse_parti_complet:
                            analyse_parti = analyse_parti_complet.split("-")[0].strip()
                        else:
                            analyse_parti = analyse_parti_complet.split(".")[0].strip()
                bloc, current = "orientation", []
            elif "3. Valeurs principales" in line:
                if bloc == "orientation": 
                    analyse_orientation_complete = "\n".join(current).strip()
                    # Extraire juste la position (ex: gauche-libertaire)
                    if analyse_orientation_complete:
                        match = re.search(r'(centre|gauche|droite)[\s-]*(libertaire|autoritaire|libéral|conservateur)?', 
                                         analyse_orientation_complete.lower())
                        if match:
                            position = match.group(1).capitalize()
                            if match.group(2):
                                position += "-" + match.group(2).capitalize()
                            analyse_orientation = position
                        else:
                            analyse_orientation = analyse_orientation_complete.split(".")[0].strip()
                bloc, current = "valeurs", []
            elif "4. Graphique ASCII" in line:
                if bloc == "valeurs": 
                    analyse_valeurs_complete = "\n".join(current).strip()
                    # Extraire la liste des valeurs (séparées par virgules ou sur des lignes différentes)
                    if analyse_valeurs_complete:
                        if "," in analyse_valeurs_complete:
                            analyse_valeurs = [v.strip() for v in analyse_valeurs_complete.split(",")]
                        else:
                            analyse_valeurs = [v.strip() for v in analyse_valeurs_complete.split("\n") if v.strip()]
                bloc, current = "graphique", []
            elif "5. Évolution d'opinion" in line or "5. Evolution d'opinion" in line:
                if bloc == "graphique": 
                    analyse_graphique = "\n".join(current).strip()
                bloc, current = "evolution", []
            else:
                current.append(line)
                
        # Traiter le dernier bloc
        if bloc == "graphique": 
            analyse_graphique = "\n".join(current).strip()
        elif bloc == "evolution":
            analyse_evolution = "\n".join(current).strip()
    
    # Formater les valeurs pour l'affichage
    analyse_valeurs_formatted = ", ".join(analyse_valeurs) if analyse_valeurs else ""
    
    # Préparer l'analyse complète pour l'affichage dans la section Analyse Détaillée
    analyse_complete = ""
    if analyse_brute:
        analyse_complete = analyse_brute
        
    if not resume_actualites:
        resume_actualites = {
            "Affaires internationales": [],
            "Économie": [],
            "Environnement": [],
            "Éducation": [],
            "Santé": [],
            "Justice": [],
            "Culture": [],
            "Technologie": []
        }
        
    # Déterminer si l'utilisateur a déjà fait un quiz complet
    has_previous_quiz = AnalysePolitique.query.filter_by(user_id=user_id).count() > 0
    
    # CORRECTIF: Passer la variable analyse_evolution au template
    return render_template(
        'dashboard.html',
        user=user,
        resume_actualites=filtered_actualites,
        analyse_parti=analyse_parti,
        analyse_orientation=analyse_orientation,
        analyse_valeurs=analyse_valeurs_formatted,
        analyse_complete=analyse_complete,
        analyse_graphique=analyse_graphique,
        analyse_evolution=analyse_evolution,  # Ajout de la variable au template
        categories=list(resume_actualites.keys()),
        has_previous_quiz=has_previous_quiz,
        quiz_en_cours=session.get('quiz_en_cours', False)  # Indiquer si un quiz est en cours
    )

#Historique des réponses
@app.route('/historique_analyses')
def historique_analyses():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user_id = session['user_id']
    
    # Récupérer toutes les analyses politiques de l'utilisateur
    analyses = AnalysePolitique.query.filter_by(user_id=user_id).order_by(AnalysePolitique.date_creation.desc()).all()
    
    return render_template(
        'historique_analyses.html',
        analyses=analyses
    )

#Sauvegarde les préférence des catégories d'articles
@app.route('/save_preferences', methods=['POST'])
def save_preferences():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    selected_categories = request.form.getlist('categories')
    user.interets = ','.join(selected_categories)
    db.session.commit()
    flash("Préférences mises à jour ✅", "success")
    return redirect(url_for('dashboard'))

# Route pour forcer le rafraîchissement manuel des actualités (option administrative)
@app.route('/refresh_actualites')
def refresh_actualites():
    """Force le rafraîchissement des actualités en vidant le cache."""
    cache.delete_memoized(fetch_actualites_cached)
    flash("Les actualités ont été rafraîchies avec succès", "success")
    return redirect(url_for('home'))


# Routes pour vérifier les questions importées par Ollama: 
#une fois les questions importées tu vas sur http://localhost:5000/admin/questions et tu coches celle que tu veux garder, les autres vont être supprimées de le Database quand t'appuiera sur Enregister 
#C'est surtout pour enlever les réponses d'ollama qui ont pas de questions ou pas de catégorie
@app.route('/admin/questions')
def admin_questions():
    # Récupère toutes les questions NON validées
    questions = Question.query.filter_by(valide=False, is_refused=False).all()
    return render_template('admin_questions.html', questions=questions)
@app.route('/admin/question/delete/<int:question_id>', methods=['POST'])
def delete_question(question_id):
    question = Question.query.get_or_404(question_id)
    db.session.delete(question)
    db.session.commit()
    return redirect(url_for('admin_questions'))
@app.route('/admin/question/validate/<int:question_id>', methods=['POST'])
def validate_question(question_id):
    question = Question.query.get_or_404(question_id)
    question.valide = True
    question.validated_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for('admin_questions'))


#Importation des actualités via http://localhost:5000/import_articles (ça active la fonction fetch en bas)
@app.route('/import_articles') 
def import_articles():
    fetch_and_process_articles()
    flash("Importation des articles et génération des questions terminée !", "success")
    return redirect(url_for('dashboard'))

#Pour voir quelles routes sont actives ou pas
@app.route('/debug_routes')
def debug_routes():
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append({
            "endpoint": rule.endpoint,
            "methods": ", ".join(list(rule.methods)),
            "rule": str(rule)
        })
    # Option 1: Retourner en JSON
    return jsonify(sorted(routes, key=lambda x: x["rule"]))


# ======================================================
# ===  Récupération Actualité + Analyse avec Ollama  ===
# ======================================================

# Fonction pour extraire le bloc JSON du texte d’Ollama
def extract_json_from_text(text):
    # Enlever des espaces ou caractères supplémentaires qui pourraient interférer
    text = text.strip().replace("\n", "").replace("\r", "")
    try:
        match = re.search(r'{.*}', text, re.DOTALL)  # Cherche un bloc JSON dans le texte
        if match:
            # Essayer de charger le JSON après avoir nettoyé le texte
            return json.loads(match.group())
        else:
            print("Aucun JSON trouvé dans le texte.")
            return None
    except json.JSONDecodeError as e:
        print(f"Erreur lors du décodage du JSON : {e}")
        return None

# Fonction pour parser le JSON généré par Ollama
def clean_and_parse_json(raw_text):
    import re
    # Nettoyer le texte pour retirer les backticks et autres caractères non JSON
    cleaned_text = raw_text.strip()
    if cleaned_text.startswith('```json'):
        cleaned_text = cleaned_text[len('```json'):].strip()  # Enlève les backticks du début
    if cleaned_text.endswith('```'):
        cleaned_text = cleaned_text[:-3].strip()  # Enlève les backticks de fin

    # Cherche le vrai bloc JSON
    json_match = re.search(r'\{.*', cleaned_text, re.DOTALL)
    if not json_match:
        print("Aucun bloc JSON détecté")
        return None

    json_str = json_match.group(0).strip()

    # Si ça finit pas par }, on le ferme manuellement
    if not json_str.endswith('}'):
        json_str += '}'

    # Essayer de parser le JSON
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"Erreur lors du parsing JSON : {e}")
        print("Contenu en erreur :", json_str)
        return None

#fonction qui récupère les actus et les donne à Ollama pour quelle renvoie la Question, La catégorie, l'url....
#ATTENTION INES, j'ai pris un compte avec l'option gratuite on peut pas faire plus de 100 rechercher par jour
#Il faut aller sur http://localhost:5000/import_articles pour l'activer
def fetch_and_process_articles():
    # Initialisation de NewsAPI
    newsapi = NewsApiClient(api_key='81ab1434b19c4ebb8517769bfbbf6cc9')

    # Dates personnalisées - étendre un peu la plage
    to_date = datetime.now().strftime('%Y-%m-%d')
    from_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')  # 15 jours au lieu de 10

    response = newsapi.get_everything(
        sources='le-monde',
        from_param=from_date,
        to=to_date,
        language='fr',
        sort_by='publishedAt',
        page_size=50,
        page=1
    )

    articles = response.get('articles', [])
    results = []
    articles_traites = 0
    articles_ignores = 0
    questions_existantes = 0
    erreurs = 0

    for article in articles:
        title = article.get('title', '')
        content = article.get('content', '') or article.get('description', '') or ''
        url = article.get('url', '')
        category = article.get('category', 'Non précisé')
        published_at = article.get('publishedAt', '')

        if not content or len(content) < 100:  # Éviter les articles trop courts
            continue

        # 1. Vérifier si l'article existe déjà dans la base de données par URL
        existing_article = Article.query.filter_by(url=url).first()
        
        if existing_article:
            # 2. Vérifier si des questions ont déjà été générées pour cet article
            existing_questions = Question.query.filter_by(article_id=existing_article.id).first()
            if existing_questions:
                questions_existantes += 1
                continue  # Article déjà traité avec questions
            else:
                # L'article existe mais pas de questions encore - on réutilise l'article
                article_obj = existing_article
        else:
            # 3. Créer un nouvel article s'il n'existe pas
            article_obj = Article(title=title, content=content, url=url, category=category, published_at=published_at)
            db.session.add(article_obj)
            try:
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"Erreur lors de la sauvegarde de l'article : {e}")
                continue

        # 4. Générer des questions avec Ollama - avec un prompt plus précis
        prompt = f"""
Tu es un assistant politique. Lis cet article et génère UNE question unique pour connaître l'opinion politique d'une personne sur le sujet.

Règles importantes:
1. La question doit être clairement liée à un enjeu politique mentionné dans l'article
2. La question doit être ouverte (pas de réponse par oui/non)
3. La question doit permettre d'identifier l'orientation politique de la personne

Réponds uniquement en JSON avec les deux clés suivantes : 
1. "categorie" : catégorie politique (choisis EXACTEMENT une seule parmi: économie, environnement, éducation, santé, affaires internationales, justice, culture, technologie).
2. "question" : question basée sur l'article, visant à connaître l'opinion d'une personne.

Exemple :
{{
    "categorie": "économie",
    "question": "Quelle est votre opinion sur les réformes fiscales proposées ?"
}}

Voici l'article : {title} - {content}
"""

        payload = {
            "model": "llama3.2",
            "prompt": prompt,
            "stream": False
        }

        try:
            response = requests.post("http://localhost:11434/api/generate", json=payload)
            
            if response.status_code == 200:
                result_text = response.json().get("response", "").strip()
                parsed_result = clean_and_parse_json(result_text)

                if not parsed_result:
                    print(f"Erreur de parsing pour l'article '{title}'")
                    erreurs += 1
                    continue

                category = parsed_result.get("categorie", "Non précisé").lower()
                question = parsed_result.get("question", "Pas de question disponible")
                
                # Vérifier que la catégorie est valide
                categories_valides = ["économie", "environnement", "éducation", "santé", 
                                    "affaires internationales", "justice", "culture", "technologie"]
                if category not in categories_valides:
                    print(f"Catégorie invalide : {category}")
                    category = "Non précisé"
                
                # 5. Vérifier si une question similaire existe déjà
                question_text_normalized = question.lower().strip()
                similar_question = Question.query.filter(
                    func.lower(Question.texte).like(f"%{question_text_normalized[5:35]}%")  # Recherche approximative
                ).first()
                
                if similar_question:
                    articles_ignores += 1
                    print(f"Question similaire déjà existante: {question[:30]}...")
                    continue
                
                # 6. Sauvegarder la nouvelle question
                try:
                    save_question(question, category, article_obj, title, url, content)
                    articles_traites += 1
                    
                    results.append({
                        "titre": title,
                        "url": url,
                        "article": content,
                        "ollama_result": {
                            "categorie": category,
                            "question": question
                        }
                    })
                except Exception as e:
                    print(f"Erreur lors de la sauvegarde de la question : {e}")
                    db.session.rollback()
            else:
                print(f"Erreur Ollama pour l'article '{title}': {response.status_code}")
                erreurs += 1
        
        except Exception as e:
            print(f"Erreur lors du traitement de l'article '{title}': {e}")
            erreurs += 1

    print(f"✅ Total : {articles_traites} questions générées, {articles_ignores} articles similaires ignorés, {questions_existantes} questions existantes, {erreurs} erreurs")
    return results

#Route de test pour le chat pour débat sur le dashboard 
@app.route('/api/dashboard/chat/test', methods=['GET'])
def test_dashboard_chat():
    """Route de test pour vérifier que l'API fonctionne"""
    try:
        print("=== TEST API CHAT APPELÉ ===")
        return jsonify({
            'success': True,
            'message': 'API dashboard chat opérationnelle',
            'timestamp': str(datetime.now()),
            'ollama_status': 'En attente de test'
        })
    except Exception as e:
        print(f"ERREUR test chat: {str(e)}")
        return jsonify({
            'error': str(e),
            'success': False
        }), 500

#Réponse d'Ollama pour les messages envoyés
def get_chat_response(prompt):
    """
    Envoie une requête à l'API Ollama avec le prompt donné et retourne la réponse.
    FONCTION CORRIGÉE avec les bons paramètres
    """
    try:
        url = "http://localhost:11434/api/generate"  
        data = {
            "model": "llama3.2",  # Spécifier le modèle
            "prompt": prompt,
            "stream": False,      # IMPORTANT: désactiver le streaming
            "temperature": 0.7,
            "max_tokens": 2000
        }
        headers = {'Content-Type': 'application/json'}
        
        logging.debug(f"Envoi de requête à Ollama avec {len(prompt)} caractères")
        
        # Timeout plus long pour Ollama
        response = requests.post(url, data=json.dumps(data), headers=headers, timeout=60)
        
        if response.status_code != 200:
            logging.error(f"Erreur HTTP {response.status_code} reçue d'Ollama: {response.text}")
            return f"Erreur lors de la communication avec Ollama. Code: {response.status_code}"
        
        try:
            result = response.json()
            return result.get("response", "Aucune réponse d'Ollama.")
        except json.JSONDecodeError as e:
            logging.error(f"Erreur de décodage JSON : {e}")
            logging.error(f"Contenu de la réponse: {response.text[:500]}")
            return "Erreur lors du traitement de la réponse JSON d'Ollama."
            
    except requests.exceptions.ConnectionError:
        logging.error("Erreur de connexion à Ollama - vérifiez que le service est en cours d'exécution")
        return "Erreur de connexion à Ollama. Veuillez vérifier que le service est démarré."
    except requests.exceptions.Timeout:
        logging.error("Timeout lors de la requête à Ollama")
        return "La requête à Ollama a expiré. Le serveur est peut-être surchargé."
    except requests.exceptions.RequestException as e:
        logging.error(f"Erreur de requête à Ollama : {e}")
        return "Erreur lors de la récupération de la réponse d'Ollama."
    except Exception as e:
        logging.error(f"Erreur inattendue lors de l'appel à Ollama : {e}")
        return "Une erreur inattendue s'est produite lors de la communication avec Ollama."

#Vrai route pour le chat de débat 
@app.route('/api/dashboard/chat', methods=['POST'])
def dashboard_chat():
    """Route pour le chat Ollama intégré au dashboard - VERSION CORRIGÉE"""
    print("=== ROUTE DASHBOARD CHAT APPELÉE ===")
    
    try:
        # Vérifier que la requête contient du JSON
        if not request.is_json:
            print("ERREUR: Requête ne contient pas de JSON")
            return jsonify({'error': 'Content-Type doit être application/json'}), 400
        
        data = request.get_json()
        print(f"Données reçues: {data}")
        
        if not data or 'message' not in data:
            print("ERREUR: Pas de message dans les données")
            return jsonify({'error': 'Message manquant'}), 400
            
        user_message = data['message'].strip()
        if not user_message:
            print("ERREUR: Message vide")
            return jsonify({'error': 'Message vide'}), 400
        
        print(f"Message reçu: {user_message}")
        
        # Construire le prompt avec un contexte politique
        prompt = f"""Tu es Politicool, un assistant politique français. 
        Réponds de manière équilibrée et informative à cette question/remarque : {user_message}
        
        Donne une réponse claire et concise (maximum 200 mots)."""
        
        print("Appel à get_chat_response...")
        
        
        response_text = get_chat_response(prompt)
        
        print(f"Réponse Ollama: {response_text[:100]}...")
        
        # Vérifier si c'est une erreur
        if response_text.startswith("Erreur"):
            print(f"ERREUR Ollama: {response_text}")
            return jsonify({'error': response_text}), 500
        
        # Succès - retourner la réponse JSON
        print("=== SUCCÈS - Retour JSON ===")
        return jsonify({
            'success': True,
            'response': response_text,
            'timestamp': str(datetime.now())
        })
        
    except Exception as e:
        print(f"EXCEPTION dans dashboard_chat: {str(e)}")
        import traceback
        traceback.print_exc()
        
        # Retourner toujours du JSON même en cas d'erreur
        return jsonify({
            'error': f'Erreur serveur: {str(e)}',
            'success': False
        }), 500

#Pour réinitialiser le chat
@app.route('/api/dashboard/chat/reset', methods=['POST'])
def reset_dashboard_chat():
    """Reset l'historique du chat du dashboard"""
    try:
        print("=== RESET CHAT APPELÉ ===")
        session.pop('dashboard_chat_history', None)
        return jsonify({
            'success': True, 
            'message': 'Chat réinitialisé'
        })
    except Exception as e:
        print(f"ERREUR reset chat: {str(e)}")
        return jsonify({
            'error': f'Erreur lors du reset: {str(e)}',
            'success': False
        }), 500

# Route simple pour tester Ollama directement
@app.route('/test-ollama', methods=['GET'])
def test_ollama_direct():
    """Test direct d'Ollama pour diagnostiquer les problèmes"""
    try:
        response = get_chat_response("Dis bonjour en français")
        return jsonify({
            'success': True,
            'ollama_response': response,
            'test_time': str(datetime.now())
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500



# ==================================
# === Lancement de l'application ===
# ==================================

if __name__ == '__main__':
    app.run(debug=True)
    
# ==============================
# ===    Thèmes    ===
# ==============================

categories = [
    "Affaires internationales"
    "Économie",
    "Environnement",
    "Éducation",
    "Santé",
    "Justice",
    "Culture",
    "Technologie"
]

