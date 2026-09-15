# UI review on every audit pass

User direction, September 14: every implementation pass must also improve the UI of the affected flow.

Inspect the real screen before editing. Make the task, current state, relevant people, and next action easy to identify. Reduce repeated explanations; use concise labels, clear grouping, spacing, and visual hierarchy. Keep optional actions subordinate to the primary task. Use consistent components and readable contrast in both themes. Preserve long names and enlarged text rather than clipping them. Distinguish intent, pending decisions, results, and physical presence accurately.

Verify the changed flow visually at 320px and a typical mobile width, in both themes, with enlarged text and keyboard interaction. Inspect the result after a successful action and after a meaningful failure or concurrent change. Record actual improvements and evidence in each feature receipt, including remaining limits. A passing functional test alone does not complete the UI review.

Every pass should answer four concrete questions in its receipt: what became easier to recognize, which unnecessary words or steps disappeared, how the mobile layout improved, and which before/after screenshots demonstrate the change. Evaluate the whole affected journey, including loading, empty, error, and success states. Keep factual labels and player counts consistent across screens. Do not add decorative changes that obscure the primary action or create extra steps.
