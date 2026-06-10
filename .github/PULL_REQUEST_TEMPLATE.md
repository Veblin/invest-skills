## Summary

<!-- Brief description of the change -->

## Type

- [ ] Bug fix
- [ ] New data source / dimension
- [ ] Knowledge base update
- [ ] Report format improvement
- [ ] Platform / harness support
- [ ] Documentation
- [ ] Other

## LAWs compliance check

- [ ] No buy/sell/hold advice (LAW 6)
- [ ] All factual claims cite sources (LAW 1)
- [ ] Facts and analysis are clearly distinguished (LAW 3)
- [ ] Single-source data is labeled (LAW 5)
- [ ] Financial data has source + timestamp (LAW 7)

## Testing

<!-- How was this tested? Include commands if applicable -->

```bash
# e.g.
uv run python -m skills.invest-A.scripts.lib.env_check
uv run python -m skills.invest-A.scripts.data_pipeline --test 600519
```

## Checklist

- [ ] No secrets/API keys committed
- [ ] `uv run pytest` passes
- [ ] Documentation updated (README, SKILL.md, AGENTS.md, CONFIGURATION.md as applicable)
