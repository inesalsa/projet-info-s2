from models import Question, Reponse, db
from datetime import datetime, timedelta 

def save_question(texte, categorie, article, title, url, content):
    # Vérifie si une question existe déjà pour cet article
    existing_question = Question.query.filter_by(article_id=article.id).first()
    
    if existing_question:
        print(f"Question déjà existante pour l'article {article.id} ({title})")
        return  # Ne crée pas de nouvelle question si elle existe déjà

    # Crée la question avec les informations nécessaires
    question = Question(texte=texte, categorie=categorie, article=article)  # texte doit être 'texte' (la question, pas le content)
    
    # Ajoute des attributs supplémentaires à la question (si nécessaire)
    question.title = title
    question.url = url
    
    # Sauvegarde la question dans la base de données
    db.session.add(question)
    db.session.commit()

def save_answer(user_id, question_id, answer_text, etat="répondu"):
    """
    Enregistre ou met à jour la réponse d'un utilisateur à une question.
    Si l'utilisateur a déjà répondu dans la session active, on met à jour.
    Si c'est un nouveau quiz, on garde l'historique des anciennes réponses.
    """
    try:
        # Vérifier si une réponse active existe déjà pour cette question dans cette session
        existing_response = Reponse.query.filter_by(
            user_id=user_id,
            question_id=question_id,
            est_active=True
        ).first()
        
        if existing_response:
            # Mettre à jour la réponse existante
            existing_response.texte = answer_text
            existing_response.etat = etat
            existing_response.date_modification = datetime.utcnow()
        else:
            # Créer une nouvelle réponse
            new_response = Reponse(
                user_id=user_id,
                question_id=question_id,
                texte=answer_text,
                etat=etat,
                est_active=True,
                date_creation=datetime.utcnow(),
                date_modification=datetime.utcnow()
            )
            db.session.add(new_response)
            
        db.session.commit()
        return True
    except Exception as e:
        db.session.rollback()
        print(f"Erreur lors de l'enregistrement de la réponse: {e}")
        return False

    
