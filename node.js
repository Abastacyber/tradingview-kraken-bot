// Import des modules nécessaires
const express = require('express');
const bodyParser = require('body-parser');
const fetch = require('node-fetch'); // Assurez-vous d'installer 'node-fetch'

// Initialisation d'Express
const app = express();
app.use(bodyParser.json());

// Endpoint pour recevoir les alertes de TradingView
app.post('/webhook', async (req, res) => {
  const signal = req.body;

  if (!signal || !signal.signal || !signal.symbol || !signal.reason) {
    console.log("Données de webhook invalides:", signal);
    return res.status(400).send('Données de webhook invalides.');
  }

  console.log(`Signal reçu pour ${signal.symbol}: ${signal.signal}`);
  console.log("Raison:", signal.reason);

  // --- Partie à personnaliser pour votre API de trading ---
  const tradingAPI_URL = "https://api.votre-service-de-trading.com/execute"; // Remplacez par l'URL de votre API
  
  // Le "payload" est le corps de la requête que vous envoyez à l'API de trading
  const payload = {
    // Les informations que votre API attend
    signal: signal.signal,
    symbol: signal.symbol,
    quantity: signal.quantity,
    reason: signal.reason
  };

  try {
    const apiResponse = await fetch(tradingAPI_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        // Ajoutez ici les en-têtes d'authentification requis par votre API de trading
        'Authorization': 'Bearer ' + process.env.API_KEY // Exemple avec une clé API
      },
      body: JSON.stringify(payload)
    });

    const result = await apiResponse.json();
    console.log("Réponse de l'API de trading:", result);
    
    // Renvoyer une réponse basée sur le succès de l'appel API
    if (apiResponse.ok) {
      res.status(200).send('Ordre envoyé à l\'API.');
    } else {
      res.status(apiResponse.status).send(`Erreur de l'API: ${apiResponse.statusText}`);
    }

  } catch (error) {
    console.error("Erreur lors de l'envoi de l'ordre à l'API:", error);
    res.status(500).send('Erreur interne du serveur.');
  }
});

// Port du serveur
const port = process.env.PORT || 8080;
app.listen(port, () => {
  console.log(`Serveur en écoute sur le port ${port}`);
});
