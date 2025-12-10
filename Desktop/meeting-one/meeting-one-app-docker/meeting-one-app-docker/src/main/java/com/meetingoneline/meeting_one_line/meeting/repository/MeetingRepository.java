package com.meetingoneline.meeting_one_line.meeting.repository;

import com.meetingoneline.meeting_one_line.meeting.entity.MeetingEntity;
import com.meetingoneline.meeting_one_line.meeting.enums.RecordSaveStatus;
import com.meetingoneline.meeting_one_line.user.UserEntity;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface MeetingRepository extends JpaRepository<MeetingEntity, UUID>, JpaSpecificationExecutor<MeetingEntity> {
    Optional<MeetingEntity> findByIdAndDeletedAtIsNull(UUID id);

    @Query("""
        SELECT m
        FROM MeetingEntity m
        WHERE m.user = :user
          AND m.status NOT IN (:excludedStatuses)
          AND m.createdAt >= :cutoffTime
        ORDER BY m.createdAt DESC
    """)
    List<MeetingEntity> findRecentIncompleteMeetings(
            @Param("user") UserEntity user,
            @Param("excludedStatuses") List<RecordSaveStatus> excludedStatuses,
            @Param("cutoffTime") LocalDateTime cutoffTime
    );
}
