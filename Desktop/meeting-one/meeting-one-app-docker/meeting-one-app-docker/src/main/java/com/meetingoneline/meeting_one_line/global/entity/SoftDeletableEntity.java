package com.meetingoneline.meeting_one_line.global.entity;

import jakarta.persistence.Column;
import jakarta.persistence.MappedSuperclass;
import lombok.Getter;
import java.time.LocalDateTime;

/**
 * soft delete 지원
 */
@Getter
@MappedSuperclass
public class SoftDeletableEntity extends BaseEntity{
    @Column(name = "deleted_at")
    private LocalDateTime deletedAt;

    public void delete() {
        this.deletedAt = LocalDateTime.now();
    }

    public boolean isDeleted() {
        return this.deletedAt != null;
    }
}
