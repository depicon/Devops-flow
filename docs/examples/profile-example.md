---
format: devops-assist-profile-v1
slug: equipe-backend
version: 1
name: Équipe backend
description: Référentiel pour les services backend maintenus par une équipe produit.
author: Équipe plateforme
created_at: '2026-11-02T00:00:00+00:00'
calms:
  questions:
    C:
    - id: responsabilites
      text: Les responsabilités de développement et d’exploitation sont-elles partagées ?
      help: Considérez les incidents et les changements de service.
    - id: retour-incidents
      text: Les incidents donnent-ils lieu à des actions suivies ?
    A:
    - id: tests
      text: Les tests sont-ils systématiquement exécutés avant intégration ?
    - id: livraison
      text: La livraison est-elle reproductible ?
    L:
    - id: petits-lots
      text: Les changements sont-ils livrés par petits lots ?
    M:
    - id: mesures
      text: L’équipe suit-elle des mesures utiles au fonctionnement du service ?
    - id: journaux
      text: Les journaux permettent-ils d’expliquer les incidents ?
    S:
    - id: procedures
      text: Les procédures utiles sont-elles accessibles à l’équipe ?
  risk_threshold: 60
  min_coverage: 0.75
rules:
- id: DOC-001
- id: DOC-002
  impact: 3
  effort: 1
- id: DOC-003
  impact: 3
  effort: 2
- id: CI-001
- id: CI-002
  impact: 3
  effort: 2
---

## Pratiques attendues

- Faire relire les changements avant intégration.
- Exécuter les tests avant chaque livraison.
- Documenter les procédures de reprise et les responsabilités.

### Configurations ciblées

Utiliser une image versionnée dans le `Dockerfile` et une exécution avec un
utilisateur adapté. Les indices techniques restent à confirmer avec l’équipe.

Ces conventions sont déclaratives : elles ne créent pas de contrôles techniques
supplémentaires et figurent dans les rapports du diagnostic.
