# Limit Reset Passes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Show available pass expiry in Codex widget and CLI.

**Architecture:** Read the Codex reset credit list. Normalize pass data in core. Render the same normalized list in both displays.

**Tech Stack:** Python standard library, GTK3, unittest.

**Spec:** `docs/superpowers/specs/2026-11-19-limit-reset-passes-design.md`

## Tasks

1. Write failing tests for available, expired, malformed, and missing credits. Add read-only fetching and normalization.
2. Write failing CLI output test. Render each available pass with local expiry and time left.
3. Add widget list and synthetic preview. Keep quota visible when pass lookup fails.
4. Run full checks, commit, push, review, merge, and deploy.
