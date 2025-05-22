from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

#Ici attention si tu veux modifier une class il faut après que dans ton terminal tu valide en faisant "flask db migrate -m [ce que tu fais (par exemple:""Ajout du champ 'valide' à Question")]"
# Et ensuite tu mets à jour avec "flask db upgrade"
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    interets = db.Column(db.String(255))  # Catégories préférées séparées par des virgules
    date_inscription = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relations
    reponses = db.relationship('Reponse', backref='user', lazy=True)
    analyses = db.relationship('AnalysePolitique', backref='user', lazy=True)


    def set_password(self, password):
        self.password_hash = generate_password_hash(password)  # Hachage du mot de passe

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)  # Vérification du mot de passe

class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    texte = db.Column(db.Text, nullable=False)
    categorie = db.Column(db.String(50), nullable=False)
    valide = db.Column(db.Boolean, default=False)
    is_refused = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    validated_at = db.Column(db.DateTime)
    
    # Relations
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'))
    article = db.relationship('Article', backref='questions')
    reponses = db.relationship('Reponse', backref='question', lazy=True)

    def __repr__(self):
        return f'<Question {self.id}>'



class Reponse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey('question.id'), nullable=False)
    texte = db.Column(db.Text, nullable=True)  # Peut être null si "passé"
    etat = db.Column(db.String(20), default="répondu")  # répondu, passé, incomplet
    est_active = db.Column(db.Boolean, default=True)  # Pour suivre les sessions de quiz
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    date_modification = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Ajout d'une contrainte unique pour éviter les doublons actifs
    __table_args__ = (
        db.UniqueConstraint('user_id', 'question_id', 'est_active', name='unique_active_response'),
    )

    # Removed the redundant relationship definition
    # user = db.relationship('User', backref='reponses')  
    
class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=True)
    url = db.Column(db.String(255), unique=True)
    category = db.Column(db.String(50))
    published_at = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __init__(self, title, content, url, category, published_at):
        self.title = title
        self.content = content
        self.url = url
        self.category = category
        self.published_at = published_at

    def __repr__(self):
        return f'<Article {self.id}>'

class AnalysePolitique(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    analyse_text = db.Column(db.Text, nullable=False)
    is_current = db.Column(db.Boolean, default=True)  # Indique si c'est l'analyse actuelle
    date_creation = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Données extraites pour faciliter la comparaison
    parti_politique = db.Column(db.String(255))
    orientation = db.Column(db.String(255))
    conservatisme = db.Column(db.Integer)  # Valeurs en pourcentage (0-100)
    socialisme = db.Column(db.Integer)
    liberalisme = db.Column(db.Integer)
    liberalisme_economique = db.Column(db.Integer)
    communisme = db.Column(db.Integer)
    fascisme = db.Column(db.Integer)
    progressisme = db.Column(db.Integer)
    nationalisme = db.Column(db.Integer)
    anarchisme = db.Column(db.Integer)
    ecologisme = db.Column(db.Integer)
    populisme = db.Column(db.Integer)
    centrisme = db.Column(db.Integer)
    
    def extract_values_from_analysis(self):
        """Extrait les valeurs numériques du graphique ASCII dans l'analyse"""
        if not self.analyse_text:
            return
            
        lines = self.analyse_text.split('\n')
        in_graph = False
        
        for line in lines:
            # Détection du début du graphique
            if "Graphique ASCII" in line:
                in_graph = True
                continue
                
            if in_graph and "|" in line and "%" in line:
                # Format attendu: "| Socialisme       ▓▓▓▓▓▓   | 60%"
                parts = line.split('|')
                if len(parts) >= 3:
                    ideology = parts[1].strip().split()[0].lower()  # Extrait "Socialisme"
                    percentage_text = parts[2].strip().replace('%', '')  # Extrait "60"
                    
                    try:
                        percentage = int(percentage_text)
                        
                        # Attribuer la valeur au bon champ
                        if 'conservatisme' in ideology:
                            self.conservatisme = percentage
                        elif 'socialisme' in ideology:
                            self.socialisme = percentage
                        elif 'liberalisme economique' in ideology or 'libéralisme économique' in ideology:
                            self.liberalisme_economique = percentage
                        elif 'liberalisme' in ideology or 'libéralisme' in ideology:
                            self.liberalisme = percentage
                        elif 'communisme' in ideology:
                            self.communisme = percentage
                        elif 'fascisme' in ideology:
                            self.fascisme = percentage
                        elif 'progressisme' in ideology:
                            self.progressisme = percentage
                        elif 'nationalisme' in ideology:
                            self.nationalisme = percentage
                        elif 'anarchisme' in ideology:
                            self.anarchisme = percentage
                        elif 'ecologisme' in ideology or 'écologisme' in ideology:
                            self.ecologisme = percentage
                        elif 'populisme' in ideology:
                            self.populisme = percentage
                        elif 'centrisme' in ideology:
                            self.centrisme = percentage
                    except (ValueError, TypeError):
                        pass  # Ignorer si la conversion échoue
            
            # Si on atteint une nouvelle section, on sort du graphique
            elif in_graph and "**" in line:
                in_graph = False
                break