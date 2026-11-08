# Confined-Space Safety Intelligence & Incident Logging Platform

A software-first prototype for collecting, validating, and monitoring simulated atmospheric sensor telemetry in confined-space environments.

The project explores how backend engineering, deterministic hazard detection, fault-aware telemetry processing, and tamper-evident incident records can contribute to a more reliable safety-monitoring system.

> **Important:** This is a research and engineering prototype using synthetic sensor data. It is not certified safety equipment and must not be used to authorize or determine real-world confined-space entry.

## Problem

Confined-space environments can expose workers to hazardous atmospheric conditions, including toxic gases, oxygen deficiency, and flammable gases.

A monitoring system must do more than collect sensor values. It should also validate incoming data, identify abnormal conditions, handle sensor failures, preserve incident history, and communicate when monitoring is unavailable.

## Project Objectives

- Simulate atmospheric sensor telemetry.
- Build a validated API for sensor-data ingestion.
- Detect hazardous conditions through explainable rules.
- Handle stale, disconnected, and invalid sensor readings.
- Maintain tamper-evident incident records.
- Explore offline-first data collection and synchronization.
- Provide a monitoring dashboard for system visibility.

## Architecture

```text
Sensor Simulator
      |
      v
FastAPI Ingestion API
      |
      v
Pydantic Validation
      |
      v
Telemetry Storage
      |
      v
Hazard Detection Engine       [Planned]
      |
      v
Incident Logging              [Planned]
      |
      v
SQLite Persistence & Sync     [Planned]
      |
      v
Monitoring Dashboard         [Planned]
