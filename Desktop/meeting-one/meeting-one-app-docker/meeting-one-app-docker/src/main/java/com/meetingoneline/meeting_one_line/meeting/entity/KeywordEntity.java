package com.meetingoneline.meeting_one_line.meeting.entity;

import com.meetingoneline.meeting_one_line.global.entity.BaseEntity;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@Entity
@Table(name = "keywords")
public class KeywordEntity extends BaseEntity {
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "meeting_id", nullable = false)
    private MeetingEntity meeting;

    @Column(nullable = false, length = 100)
    private String keyword;

    public static KeywordEntity create(MeetingEntity meeting, String keyword){
        KeywordEntity _keyword = new KeywordEntity();
        _keyword.meeting = meeting;
        _keyword.keyword = keyword;

        return _keyword;
    }
}
