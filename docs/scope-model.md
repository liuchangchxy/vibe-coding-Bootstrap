# Scope model

Every registered component has one explicit scope. Scope records where a person may choose to use a component in a future installation; it does not perform or authorize deployment.

## Global

Global components are candidates for a person's shared environment: long-term preferences, cross-project Skills, general AI work habits, or an MCP the person chooses to use globally. Global does not mean automatically installed in every project. A project may discover or reference a shared component without enabling it.

## Project

Project components belong only to the current project, such as its rules, architecture documents, prompts, Skill configuration, or MCP configuration. Project material must not be promoted to Global automatically. A human must make that separate decision.

## External

External components are maintained by a third party, such as an open-source Skill or official template. This repository records a source, provider, version or its unknown state, applicability, provenance, and delivery hint. It keeps a reference rather than a maintained copy of upstream content.

## Discovery is not activation

Global discoverability means a shared component can be considered while assembling a project. It does not mean that the component is active, copied into the project, or installed. The Assembly Plan makes that distinction visible and requires human confirmation.
