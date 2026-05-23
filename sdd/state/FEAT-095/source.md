---
kind: inline
jira_key: null
fetched_at: 2026-05-19T23:45:00Z
summary_oneline: MultiQuery documentation system with base class normalization, CLI generation, HTTP API for listing and validation
---

# Documentation of MultiQuery

All MultiQuery objects inherit from one of these classes:
- AbstractTransform (all transformations)
- AbstractOperator (all operators as Join, Melt, Concat)
- TableOutput (AbstractDestination)
- ThreadSource

Idea is providing a base class for all of Operators, Transforms and components with common methods and one special method, like a "reflection" method, returning all "public" attributes to build a comprehensive documentation about all components, Transformations, Operators, Sources and Outputs.

Take as example the code in `../flowtask/flowtask/documentation/` to build a documentation composed by:
- JSON schema of attributes
- Documentation with name, usage and description
- Example code

## Tasks
- Normalize the current AbstractTransform, AbstractOperator, AbstractComponent to be inherited by a upper AbstractMulti with common methods shared by all of them (as async context methods) and one class method for returning all the public attributes of the class, to be used for documentation.
  * ThreadSource and AbstractDestination will be deferred to next feature because ThreadSource is currently be developed by FEAT-093 (not in dev yet).
- Add a detailed classdoc to every Operator, Transform, Source or Output (destination) explaining the usage, description and one example in JSON format.
- create a classmethod for extracting all public attributes and types based on type-hint (if no type-hint, then use "Any")
- Generate a CLI command for extracting the in-file documentation and classdoc and create a `generated/` folder with per-component documentation with:
  - Component Name
  - Component Description
  - Component Usage
  - Category (Sources, Destinations, Transformations, Operations, Components)
  - JSON Schema describing the component
  - List of attributes supported
  - Example in JSON format.
- Generate an HTTP GET method for serving the list of all components supported by Multi-Query
- Based on description, usage and attributes supported, create a POST method for validating a JSON-payload if is a valid MultiQuery Data-pipeline definition, method will receive a JSON payload and that payload will be validate if is a syntactically valid Multi-Query Job pipeline.
