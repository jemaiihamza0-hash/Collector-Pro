# Validation de cette livraison

- 19 tests réussis, 1 test navigateur ignoré.
- Collecte HTTP testée sur un véritable serveur local de test avec un processus Scrapy : pagination, PDF, DOCX, rejet du faux PDF, archivage, extraction et téléchargement via API.
- Vérification Python Ruff et syntaxe JavaScript réussies.
- Tests exécutés sur SQLite. Compilation du schéma et des verrous SQL PostgreSQL vérifiée, sans serveur PostgreSQL réel.
- Docker Compose, PostgreSQL réel, Chromium et Docling ne sont pas validés en exécution ici. Le téléchargement du navigateur a échoué dans cet environnement ; le test navigateur est donc explicitement ignoré.
- Aucune exhaustivité sur le site CMF réel n’est certifiée. La couverture dépend des liens accessibles, de la pagination, des formulaires et des refus du serveur.
- Un avertissement de dépréciation FastAPI/Starlette relatif au client de test HTTPX est présent ; il ne fait pas échouer les tests.
