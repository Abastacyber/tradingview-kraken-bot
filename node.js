// Import des modules nécessaires
const express = require('express');
const bodyParser = require('body-parser');
const admin = require('firebase-admin');

// Initialisation d'Express
const app = express();
app.use(bodyParser.json());

// Récupération sécurisée des variables d'environnement
// IMPORTANT : La variable d'environnement doit être une chaîne JSON valide
let serviceAccount;
try {
  serviceAccount = JSON.parse(process.env.FIREBASE_ADMIN_SDK);
} catch (error) {
  console.error("Erreur lors de l'analyse de FIREBASE_ADMIN_SDK. Assurez-vous que c'est une chaîne JSON valide.");
  process.exit(1); // Arrête le processus si la clé n'est pas valide
}

// Initialisation de Firebase Admin SDK
admin.initializeApp({
  credential: admin.credential.cert(serviceAccount)
});

const db = admin.firestore();

// Endpoint pour recevoir les alertes de TradingView
app.post('/webhook', async (req, res) => {
  const signal = req.body;

  if (!signal || !signal.signal || !signal.symbol || !signal.reason) {
    console.log("Données de webhook invalides:", signal);
    return res.status(400).send('Données de webhook invalides.');
  }

  console.log(`Signal reçu pour ${signal.symbol}: ${signal.signal}`);
  console.log("Raison:", signal.reason);

  const timestamp = new Date().toISOString();
  
  // Chemin de la collection Firestore
  const collectionPath = `signals/${signal.symbol}`;
  const signalDoc = {
    ...signal,
    timestamp: timestamp,
    // ID unique pour le document
    id: db.collection(collectionPath).doc().id,
  };
  
  try {
    await db.collection(collectionPath).add(signalDoc);
    console.log("Signal enregistré dans Firestore.");
    res.status(200).send('OK');
  } catch (error) {
    console.error("Erreur lors de l'écriture dans Firestore:", error);
    res.status(500).send('Erreur interne du serveur');
  }
});

// Port du serveur
const port = process.env.PORT || 8080;
app.listen(port, () => {
  console.log(`Serveur en écoute sur le port ${port}`);
});
