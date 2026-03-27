"""Bayesian updating — adjust probabilities when new evidence arrives.

P(H|E) = P(E|H) * P(H) / P(E)

Use case: scanner finds a signal at z=2.5, then Claude says "this market
diverged because of breaking news." The Bayesian update downgrades the
reversion probability because the divergence may be fundamental, not noise.
"""
import logging

log = logging.getLogger("scanner.bayes")


def update(prior, likelihood_given_true, likelihood_given_false):
    """Bayes' theorem — update probability with new evidence.

    Args:
        prior: P(H) — our current belief (e.g., 0.85 probability of reversion)
        likelihood_given_true: P(E|H) — how likely is this evidence if H is true
        likelihood_given_false: P(E|~H) — how likely is this evidence if H is false

    Returns:
        posterior: P(H|E) — updated belief

    Example:
        Scanner says 85% reversion probability.
        Claude says "breaking news makes reversion unlikely."
        P(claude_says_this | reversion_happens) = 0.2 (unlikely Claude would warn if it reverts)
        P(claude_says_this | no_reversion) = 0.8 (likely Claude warns when it won't revert)

        posterior = update(0.85, 0.2, 0.8)
        # Result: ~0.53 — significantly downgraded
    """
    if prior <= 0 or prior >= 1:
        return prior

    p_evidence = likelihood_given_true * prior + likelihood_given_false * (1 - prior)

    if p_evidence <= 0:
        return prior

    posterior = (likelihood_given_true * prior) / p_evidence

    log.debug("Bayes update: prior=%.3f → posterior=%.3f (L_true=%.2f, L_false=%.2f)",
              prior, posterior, likelihood_given_true, likelihood_given_false)

    return posterior


def update_with_brain(prior_prob, brain_result):
    """Update reversion probability using Claude's assessment.

    Maps Claude's confidence and edge estimate to likelihood ratios.
    """
    if not brain_result:
        return prior_prob

    confidence = brain_result.get("confidence", "low")
    edge = brain_result.get("edge_vs_market", 0)

    # If Claude sees edge in SAME direction as our signal → confirms
    # If Claude sees edge AGAINST our signal → disconfirms
    if edge > 0.05:
        # Claude agrees there's mispricing — evidence supports reversion
        if confidence == "high":
            return update(prior_prob, 0.9, 0.3)  # strong confirmation
        elif confidence == "medium":
            return update(prior_prob, 0.7, 0.4)  # moderate confirmation
        else:
            return update(prior_prob, 0.55, 0.45)  # weak, barely moves

    elif edge < -0.05:
        # Claude thinks market is correctly priced — evidence against reversion
        if confidence == "high":
            return update(prior_prob, 0.2, 0.8)  # strong disconfirmation
        elif confidence == "medium":
            return update(prior_prob, 0.35, 0.65)
        else:
            return update(prior_prob, 0.45, 0.55)

    # No significant edge either way
    return prior_prob


def chain_updates(prior, evidence_list):
    """Chain multiple Bayesian updates.

    Each evidence item is (likelihood_true, likelihood_false).
    Order doesn't matter mathematically, but we process sequentially.
    """
    current = prior
    for lt, lf in evidence_list:
        current = update(current, lt, lf)
    return current
