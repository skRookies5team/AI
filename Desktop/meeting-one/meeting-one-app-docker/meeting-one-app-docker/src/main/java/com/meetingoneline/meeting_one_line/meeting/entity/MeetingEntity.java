package com.meetingoneline.meeting_one_line.meeting.entity;

import com.meetingoneline.meeting_one_line.global.entity.SoftDeletableEntity;
import com.meetingoneline.meeting_one_line.meeting.enums.RecordSaveStatus;
import com.meetingoneline.meeting_one_line.user.UserEntity;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.SQLRestriction;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@Entity
@Setter
@Table(name = "meetings")
@SQLRestriction("deleted_at IS NULL")
public class MeetingEntity extends SoftDeletableEntity {
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private UserEntity user;

    @Column(nullable = false, length = 255)
    private String title;

    @Column(nullable = false)
    private LocalDateTime date;

    @Column(name = "file_path", nullable = false, length = 500)
    private String filePath;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private RecordSaveStatus status;

    @Column(columnDefinition = "TEXT")
    private String summary;

    // 연관관계
    @OneToMany(mappedBy = "meeting", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<SpeakerEntity> speakers = new ArrayList<>();

    @OneToMany(mappedBy = "meeting", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<KeywordEntity> keywords = new ArrayList<>();

    /**
     * meeting entity 생성
     */
    public static MeetingEntity create(UserEntity user, String title, LocalDateTime date, String filePath) {
        MeetingEntity meeting = new MeetingEntity();
        meeting.user = user;
        meeting.title = title;
        meeting.date = date;
        meeting.filePath = filePath;
        meeting.status = RecordSaveStatus.UPLOADED;

        return meeting;
    }

    /**
     * metting status 변경
     */
    public void updateStatusAndSummary(String status, String summary) {
        try {
            this.status = RecordSaveStatus.valueOf(status.toUpperCase());
        } catch (IllegalArgumentException e) {
            this.status = RecordSaveStatus.PROCESSING; // fallback
        }
        this.summary = summary;
    }
}
