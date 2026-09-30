# -*- coding: utf-8 -*-
"""动态技能数据访问层"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from true_love_ai.models.dynamic_skill import DynamicSkill

LOG = logging.getLogger("DynamicSkillRepository")


class DynamicSkillRepository:

    def __init__(self, session: Session):
        self.session = session

    def save(self, id: str, name: str, description: str, command: str,
             parameters: str | None, creator: str | None) -> bool:
        try:
            existing = self.session.get(DynamicSkill, id)
            if existing:
                existing.name = name
                existing.description = description
                existing.command = command
                existing.parameters = parameters
                existing.creator = creator
            else:
                self.session.add(DynamicSkill(
                    id=id,
                    name=name,
                    description=description,
                    command=command,
                    parameters=parameters,
                    creator=creator,
                ))
            self.session.commit()
            return True
        except Exception as e:
            self.session.rollback()
            LOG.error("save dynamic skill 失败: id=%s err=%s", id, e)
            return False

    def get(self, id: str) -> DynamicSkill | None:
        return self.session.get(DynamicSkill, id)

    def list_all(self) -> list[DynamicSkill]:
        return self.session.query(DynamicSkill).order_by(DynamicSkill.id).all()

    def increment_usage(self, id: str) -> None:
        try:
            skill = self.session.get(DynamicSkill, id)
            if skill:
                skill.usage_count += 1
                skill.last_used_at = datetime.now()
                self.session.commit()
        except Exception as e:
            self.session.rollback()
            LOG.warning("increment_usage 失败: id=%s err=%s", id, e)

    def delete(self, id: str) -> bool:
        try:
            skill = self.session.get(DynamicSkill, id)
            if not skill:
                return False
            self.session.delete(skill)
            self.session.commit()
            return True
        except Exception as e:
            self.session.rollback()
            LOG.error("delete dynamic skill 失败: id=%s err=%s", id, e)
            return False
